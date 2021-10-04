### global imports, maybe not all of them are completely necessary, but who cares?
import torch
import numpy as np
import torch.nn as nn
import torch.nn.functional as F
import math
import time
import random
import sys
print("Using pytorch version: " + torch.__version__)

### local imports, dirty and should definitely be improved in the future
print("Importing local files...", end = '')
from args import args
from utils import *
from datasets import *
from simpleshot import *
from resnet import *
from wideresnet import *
from resnet12 import *
from s2m2 import *
from mlp import *
print("done")

### global variables that are used by the train function
last_update, criterion = 0, torch.nn.CrossEntropyLoss()
def train(model, train_loader, optimizer, epoch, mixup = False):
    model.train()
    global last_update
    
    losses = 0  # to keep track of the training loss
    total = 0
    
    for batch_idx, (data, target) in enumerate(train_loader):
            
        data, target = data.to(args.device), target.to(args.device)

        # reset gradients
        optimizer.zero_grad()

        if args.rotations:
            bs = data.shape[0] // 4
            target_rot = torch.LongTensor(data.shape[0]).to(args.device)
            target_rot[:bs] = 0
            data[bs:] = data[bs:].transpose(3,2).flip(2)
            target_rot[bs:2*bs] = 1
            data[2*bs:] = data[2*bs:].transpose(3,2).flip(2)
            target_rot[2*bs:3*bs] = 2
            data[3*bs:] = data[3*bs:].transpose(3,2).flip(2)
            target_rot[3*bs:] = 3

        if mixup:
            index_mixup = torch.randperm(data.shape[0])
            lam = random.random()
            data_mixed = lam * data + (1 - lam) * data[index_mixup]
            output, _ = model(data_mixed)
            if args.rotations:
                output, output_rot = output
                loss = ((lam * criterion(output, target) + (1 - lam) * criterion(output, target[index_mixup])) + (lam * criterion(output_rot, target_rot) + (1 - lam) * criterion(output_rot, target_rot[index_mixup]))) / 2
            else:
                loss = lam * criterion(output, target) + (1 - lam) * criterion(output, target[index_mixup])
        else:
            output, _ = model(data)
            if args.rotations:
                output, output_rot = output
                loss = 0.5 * criterion(output, target) + 0.5 * criterion(output_rot, target_rot)
            else:
                loss = criterion(output, target)

        # backprop loss
        loss.backward()
            
        losses += loss.item() * data.shape[0]
        total += data.shape[0]

        # update parameters
        optimizer.step()

        # print advances if at least 100ms have passed since last print
        if (batch_idx + 1 == len(train_loader)) or (time.time() - last_update > 0.1) and not args.quiet:
            if batch_idx + 1 < len(train_loader):
                print("\r{:4d} {:4d}/{:4d} loss: {:.5f} time: {:s} ".format(epoch, 1 + batch_idx, len(train_loader), losses / total, format_time(time.time() - start_time)), end = "")
            else:
                print("\r{:4d} loss: {:.5f} ".format(epoch, losses / total), end = '')
            last_update = time.time()

    # return train_loss
    return { "train_loss" : losses / total}

# function to compute accuracy in the case of standard classification
def test(model, test_loader):
    model.eval()
    test_loss, accuracy, accuracy_top_5, total = 0, 0, 0, 0
    
    with torch.no_grad():
        for data, target in test_loader:
            data, target = data.to(args.device), target.to(args.device)
            output, _ = model(data)
            if args.rotations:
                output, _ = output
            test_loss += criterion(output, target).item() * data.shape[0]
            pred = output.argmax(dim=1, keepdim=True)
            accuracy += pred.eq(target.view_as(pred)).sum().item()
            
            # if we want to compute top-5 accuracy
            if top_5:
                preds = output.sort(dim = 1, descending = True)[1][:,:5]
                for i in range(preds.shape[0]):
                    if target[i] in preds[i]:
                        accuracy_top_5 += 1
            # count total number of samples for averaging in the end
            total += target.shape[0]

    # return results
    model.train()
    return { "test_loss" : test_loss / total, "test_acc" : accuracy / total, "test_acc_top_5" : accuracy_top_5 / total}

# function to train a model using args.epochs epochs
# at each args.milestones, learning rate is multiplied by args.gamma
def train_complete(model, loaders, mixup = False):
    global start_time
    start_time = time.time()
    
    train_loader, val_loader, test_loader = loaders

    lr = args.lr
    
    if few_shot:
        few_shot_meta_data["best_val_acc_1"] = 0
        few_shot_meta_data["best_val_acc_5"] = 0

    if lr < 0:
        optimizer = torch.optim.Adam(model.parameters(), lr = -1 * lr)
    else:
        optimizer = torch.optim.SGD(model.parameters(), lr = lr, momentum = 0.9, weight_decay = 5e-4, nesterov = True)

    scheduler = torch.optim.lr_scheduler.MultiStepLR(optimizer, milestones = eval(args.milestones), gamma = args.gamma)
        
        
    for epoch in range(args.epochs):

        train_stats = train(model, train_loader, optimizer, (epoch + 1), mixup = mixup)

        scheduler.step()
        
        if args.save_model != "" and not few_shot:
            torch.save(model, args.save_model)
        
        if (epoch + 1) > args.skip_epochs:
            if few_shot:
                val_acc_1, test_acc_1, val_acc_5, test_acc_5 = update_few_shot_meta_data(model, test_loader, val_loader, few_shot_meta_data)
                print("val-1: {:.2f}%, nov-1: {:.2f}% ({:.2f}%), val-5: {:.2f}%, nov-5: {:.2f}% ({:.2f}%)".format(100 * val_acc_1, 100 * test_acc_1, 100 * few_shot_meta_data["best_test_acc_1"], 100 * val_acc_5, 100 * test_acc_5, 100 * few_shot_meta_data["best_test_acc_5"]))
            else:
                test_stats = test(model, test_loader)
                if top_5:
                    print("top-1: {:.2f}%, top-5: {:.2f}%".format(100 * test_stats["test_acc"], 100 * test_stats["top_5"]))
                else:
                    print("test acc: {:.2f}%".format(100 * test_stats["test_acc"]))

    if few_shot:
        return few_shot_meta_data
    else:
        return test_stats

### process main arguments
loaders, input_shape, num_classes, few_shot, top_5 = get_dataset(args.dataset)
### initialize few-shot meta data
if few_shot:
    num_classes, val_classes, novel_classes, elements_per_class = num_classes
    if args.dataset.lower() == "cubfs":
        elements_val, elements_novel = elements_per_class
    else:
        elements_val, elements_novel = [elements_per_class] * val_classes, [elements_per_class] * novel_classes
    print("Dataset contains",num_classes,"base classes,",val_classes,"val classes and",novel_classes,"novel classes.")
    val_run_classes, val_run_indices = define_runs(n_ways, n_shots, n_queries, val_classes, elements_val)
    novel_run_classes, novel_run_indices = define_runs(n_ways, n_shots, n_queries, novel_classes, elements_novel)
    few_shot_meta_data = {
        "val_run_classes" : val_run_classes,
        "val_run_indices" : val_run_indices,
        "novel_run_classes" : novel_run_classes,
        "novel_run_indices" : novel_run_indices,
        "best_val_acc_5" : 0,
        "best_val_acc_1" : 0,
        "best_test_acc_5" : 0,
        "best_test_acc_1" : 0
    }

# can be used to compute mean and std on training data, to adjust normalizing factors
if False:
    train_loader, _, _ = loaders
    try:
        for c in range(input_shape[0]):
            print("Mean of canal {:d}: {:f} and std: {:f}".format(c, train_loader.data[:,c,:,:].reshape(train_loader.data[:,c,:,:].shape[0], -1).mean(), train_loader.data[:,c,:,:].reshape(train_loader.data[:,c,:,:].shape[0], -1).std()))
    except:
        pass

### prepare stats
run_stats = {}
if args.output != "":
    f = open(args.output, "a")
    f.write(str(args))
    f.close()

### function to create model
def create_model():
    if args.model.lower() == "resnet18":
        return ResNet(BasicBlock, [2, 2, 2, 2], args.feature_maps, input_shape, num_classes, few_shot, args.rotations).to(args.device)
    if args.model.lower() == "resnet20":
        return ResNet(BasicBlock, [3, 3, 3], args.feature_maps, input_shape, num_classes, few_shot, args.rotations).to(args.device)
    if args.model.lower() == "wideresnet":
        return WideResNet(args.feature_maps, input_shape, few_shot, args.rotations, num_classes = num_classes).to(args.device)
    if args.model.lower() == "resnet12":
        return ResNet12(args.feature_maps, input_shape, num_classes, few_shot, args.rotations).to(args.device)
    if args.model.lower()[:3] == "mlp":
        return MLP(args.feature_maps, int(args.model[3:]), input_shape, num_classes, args.rotations, few_shot).to(args.device)
    if args.model.lower() == "s2m2r":
        return S2M2R(args.feature_maps, input_shape, args.rotations, num_classes = num_classes).to(args.device)
    
if args.load_model != "":
    model = torch.load(args.load_model).to(args.device)

if args.test_features != "":
    test_features = torch.load(args.test_features)
    print("Testing features of shape", test_features.shape)
    perf1 = 100 * ncm(test_features, few_shot_meta_data["novel_run_classes"], few_shot_meta_data["novel_run_indices"], 1)
    perf5 = 100 * ncm(test_features, few_shot_meta_data["novel_run_classes"], few_shot_meta_data["novel_run_indices"], 5)
    print("1-shot: {:.2f}%, 5-shot: {:.2f}%".format(perf1, perf5))
    sys.exit()

### here a run is a complete training of a model from scratch
### can be long if run is large!!!
for i in range(args.runs):
    if not args.quiet:
        print(args)
    model = create_model()
    
    if i == 0:
        print("Number of trainable parameters in model is: " + str(np.sum([p.numel() for p in model.parameters()])))

    # training
    test_stats = train_complete(model, loaders, mixup = args.mixup)

    # assemble stats
    for item in test_stats.keys():
        if i == 0:
            run_stats[item] = [test_stats[item]]
        else:
            run_stats[item].append(test_stats[item])

    # write file output 
    if args.output != "":
        f = open(args.output, "a")
        f.write(", " + str(run_stats))
        f.close()

    # print stats
    print("Run", i + 1, "/", args.runs)
    if few_shot:
        mean, low, up = stats(run_stats["best_test_acc_1"])
        print("Top-1: {:.2f} (conf: [{:.2f}, {:.2f}])".format(100 * mean, 100 * low, 100 * up))
        mean, low, up = stats(run_stats["best_test_acc_5"])
        print("Top-5: {:.2f} (conf: [{:.2f}, {:.2f}])".format(100 * mean, 100 * low, 100 * up))
    else:
        mean, low, up = stats(run_stats["test_acc"])
        print("Top-1: {:.2f} (conf: [{:.2f}, {:.2f}])".format(100 * mean, 100 * low, 100 * up))
        if top_5:
            mean, low, up = stats(run_stats["test_acc_top_5"])
            print("Top-5: {:.2f} (conf: [{:.2f}, {:.2f}])".format(100 * mean, 100 * low, 100 * up))

if args.output != "":
    f = open(args.output, "a")
    f.write("\n")
    f.close()
