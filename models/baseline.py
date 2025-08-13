#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@author: Jisheng Bai, Haohe Liu, Han Yin, Mou Wang
@email: baijs@xupt.edu.cn
# Xi'an University of Posts & Telecommunications, China
# Joint Laboratory of Environmental Sound Sensing, School of Marine Science and Technology, Northwestern Polytechnical University, China
# Xi'an Lianfeng Acoustic Technologies Co., Ltd., China
# University of Surrey, UK
# This software is distributed under the terms of the License MIT
"""
import torch
import torch.nn as nn
import numpy as np
import math
import torch.nn.functional as F
from tqdm import tqdm
import os

from models.mixup import mixup_data, mixup_criterion
from torch.autograd import Variable


def init_layer(layer):
    """Initialize a Linear or Convolutional layer."""
    nn.init.xavier_uniform_(layer.weight)

    if hasattr(layer, "bias"):
        if layer.bias is not None:
            layer.bias.data.fill_(0.0)


def init_bn(bn):
    """Initialize a Batchnorm layer."""
    bn.bias.data.fill_(0.0)
    bn.weight.data.fill_(1.0)


def init_gru(rnn):
    """Initialize a GRU layer."""

    def _concat_init(tensor, init_funcs):
        (length, fan_out) = tensor.shape
        fan_in = length // len(init_funcs)

        for i, init_func in enumerate(init_funcs):
            init_func(tensor[i * fan_in: (i + 1) * fan_in, :])

    def _inner_uniform(tensor):
        fan_in = nn.init._calculate_correct_fan(tensor, "fan_in")
        nn.init.uniform_(tensor, -math.sqrt(3 / fan_in), math.sqrt(3 / fan_in))

    for i in range(rnn.num_layers):
        _concat_init(
            getattr(rnn, "weight_ih_l{}".format(i)),
            [_inner_uniform, _inner_uniform, _inner_uniform],
        )
        torch.nn.init.constant_(getattr(rnn, "bias_ih_l{}".format(i)), 0)

        _concat_init(
            getattr(rnn, "weight_hh_l{}".format(i)),
            [_inner_uniform, _inner_uniform, nn.init.orthogonal_],
        )
        torch.nn.init.constant_(getattr(rnn, "bias_hh_l{}".format(i)), 0)


class ConvBlock(nn.Module):
    def __init__(self, in_channels, out_channels):
        """
        param in_channels: the number of input channels
        param out_channels: the number of out channels
        """
        super(ConvBlock, self).__init__()

        self.conv1 = nn.Conv2d(
            in_channels=in_channels,
            out_channels=out_channels,
            kernel_size=(3, 3),
            stride=(1, 1),
            padding=(1, 1),
            bias=False,
        )

        self.conv2 = nn.Conv2d(
            in_channels=out_channels,
            out_channels=out_channels,
            kernel_size=(3, 3),
            stride=(1, 1),
            padding=(1, 1),
            bias=False,
        )

        self.bn1 = nn.BatchNorm2d(out_channels)
        self.bn2 = nn.BatchNorm2d(out_channels)

        self.init_weight()

    def init_weight(self):
        init_layer(self.conv1)
        init_layer(self.conv2)
        init_bn(self.bn1)
        init_bn(self.bn2)

    def forward(self, input, pool_size=(2, 2), pool_type="avg"):
        x = input
        x = F.relu_(self.bn1(self.conv1(x)))
        x = F.relu_(self.bn2(self.conv2(x)))
        if pool_type == "max":
            x = F.max_pool2d(x, kernel_size=pool_size)
        elif pool_type == "avg":
            x = F.avg_pool2d(x, kernel_size=pool_size)
        elif pool_type == "avg+max":
            x1 = F.avg_pool2d(x, kernel_size=pool_size)
            x2 = F.max_pool2d(x, kernel_size=pool_size)
            x = x1 + x2
        else:
            raise Exception("Incorrect argument!")

        return x


class SELayer(nn.Module):
    def __init__(self, channel, reduction=16):
        """
        param channel: the number of input channels
        """
        super(SELayer, self).__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Sequential(
            nn.Linear(channel, channel // reduction, bias=False),
            nn.ReLU(inplace=True),
            nn.Linear(channel // reduction, channel, bias=False),
            nn.Sigmoid(),
        )

    def forward(self, x):
        b, c, _, _ = x.size()
        y = self.avg_pool(x).view(b, c)
        self.attn_vec = y
        y = self.fc(y).view(b, c, 1, 1)
        return x * y.expand_as(x)


class SEBlock(nn.Module):
    def __init__(self, in_channels, out_channels):
        """
        param in_channels: the number of input channels
        param out_channels: the number of out channels
        """
        super(SEBlock, self).__init__()

        self.conv1 = nn.Conv2d(
            in_channels=in_channels,
            out_channels=out_channels,
            kernel_size=(3, 3),
            stride=(1, 1),
            padding=(1, 1),
            bias=False,
        )

        self.conv2 = nn.Conv2d(
            in_channels=out_channels,
            out_channels=out_channels,
            kernel_size=(3, 3),
            stride=(1, 1),
            padding=(1, 1),
            bias=False,
        )

        self.bn1 = nn.BatchNorm2d(out_channels)
        self.bn2 = nn.BatchNorm2d(out_channels)

        self.se1 = SELayer(out_channels)
        self.se2 = SELayer(out_channels)

        self.init_weight()

    def init_weight(self):
        init_layer(self.conv1)
        init_layer(self.conv2)
        init_bn(self.bn1)
        init_bn(self.bn2)

    def forward(self, input, pool_size=(2, 2), pool_type="avg"):
        x = input
        x = F.relu_(self.se1(self.bn1(self.conv1(x))))
        x = F.relu_(self.se2(self.bn2(self.conv2(x))))
        if pool_type == "max":
            x = F.max_pool2d(x, kernel_size=pool_size)
        elif pool_type == "avg":
            x = F.avg_pool2d(x, kernel_size=pool_size)
        elif pool_type == "avg+max":
            x1 = F.avg_pool2d(x, kernel_size=pool_size)
            x2 = F.max_pool2d(x, kernel_size=pool_size)
            x = x1 + x2
        else:
            raise Exception("Incorrect argument!")

        return x


class SE_Trans(nn.Module):
    def __init__(
            self, frames, bins, class_num, nhead, dim_feedforward, n_layers, dropout,
            enable_multimodal=False, num_locations=10, location_embedding_dim=16,
            time_feature_dim=8, time_mapping_dim=16
    ):
        """
        :param enable_multimodal: enable multimodal ASC
        :param num_locations: number of city
        :param location_embedding_dim: city embedding dim
        :param time_feature_dim: time feature dim
        :param time_mapping_dim: time embedding dim
        """
        super(SE_Trans, self).__init__()

        self.enable_multimodal = enable_multimodal

        self.SE_block1 = SEBlock(in_channels=1, out_channels=64)
        self.SE_block2 = SEBlock(in_channels=64, out_channels=128)
        self.global_pool = nn.AdaptiveAvgPool2d((16, 1))

        self.encoder_layer = nn.TransformerEncoderLayer(
            128, nhead, dim_feedforward, dropout
        )
        self.encoder = nn.TransformerEncoder(self.encoder_layer, n_layers)

        if self.enable_multimodal:
            self.location_embedding = nn.Embedding(num_locations, location_embedding_dim)
            self.time_mapping = nn.Linear(time_feature_dim, time_mapping_dim)
            fused_dim = 128 + location_embedding_dim + time_mapping_dim
            self.fusion_layer = nn.Linear(fused_dim, 128)
            self.fc = nn.Linear(128, class_num, bias=True)
        else:
            self.fc = nn.Linear(128, class_num, bias=True)

        self.bn0 = nn.BatchNorm2d(bins)
        self.init_weights()

    def init_weights(self):
        init_bn(self.bn0)
        init_layer(self.fc)
        if self.enable_multimodal:
            init_layer(self.time_mapping)
            init_layer(self.fusion_layer)

    def forward(self, x, locations=None, time_features=None):
        """
        :param x: audio features [batch, frames, bins, 1]
        :param locations: city [batch]
        :param time_features: time feature [batch, 8]
        """
        # print(x.size())
        # assert 1 == 2
        x = x.transpose(1, 3)  # x = [batch, bins, frames, in_chs]
        x = self.bn0(x)  # BN is done over the bins dimension
        x = x.transpose(1, 3)  # x = [batch, in_chs, frames, bins]

        x = self.SE_block1(x, pool_size=(2, 2), pool_type="avg")
        x = self.SE_block2(x, pool_size=(2, 2), pool_type="avg")

        x = self.global_pool(x)
        x = x.view(x.size(0), -1, x.size(2))  # x = [batch, in_chs, frames]
        x = x.permute(2, 0, 1)  # x = [frames, batch, in_chs]
        x = self.encoder(x)  # x = [frames, batch, in_chs]
        x = x.permute(1, 0, 2)  # x = [batch, frames, in_chs]
        # print(x.size())
        # assert 1 == 2
        (x, _) = torch.max(x, dim=1)  # (batch_size, in_chs=128)

        if self.enable_multimodal and locations is not None and time_features is not None:
            loc_emb = self.location_embedding(locations)  # [batch, location_embedding_dim]

            time_emb = self.time_mapping(time_features)  # [batch, time_mapping_dim]
            time_emb = torch.relu(time_emb)

            fused_features = torch.cat([x, loc_emb, time_emb], dim=1)  # [batch, 128+16+16]
            x = self.fusion_layer(fused_features)  # [batch, 128]
            x = torch.relu(x)

        x = self.fc(x)
        return x


class ModelTrainer(object):
    def __init__(
            self,
            model: nn.Module,
            criterion,
            device: str,
            metric,
            optimizer,
            scheduler,
    ):
        self.device = device
        self.model = model.to(self.device)
        self.criterion = criterion
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.metric = metric.to(self.device)

    def fit(
            self,
            train_data,
            valid_data,
            epochs,
            exp_path,
            model_file_name,
            early_stopping,
            pre_train=False
    ):
        best_metric, best_epoch = 0, 0
        count = 0
        print("== Training start ==")
        for _epoch in range(epochs):
            train_loss = self.train_epoch(train_data, pre_train)
            if valid_data is not None:
                val_score = self.val_epoch(valid_data)
                print(
                    f"epoch:{_epoch}, train_loss:{train_loss:.3f}, val_acc:{val_score:.3f}"
                )

                count += 1

                if val_score >= best_metric:
                    best_metric = val_score
                    count = 0
                    best_epoch = _epoch
                    checkpoint = {
                        "epoch": _epoch,
                        "model_state_dict": self.model.state_dict(),
                        "optimizer_state_dict": self.optimizer.state_dict(),
                    }
                    torch.save(checkpoint, os.path.join(exp_path, model_file_name))
                if early_stopping is not None:
                    if count >= early_stopping:
                        break

        print(f"best_epoch:{best_epoch}, best_metric:{best_metric:.3f}\n")

    def train_epoch(self, train_data, pre_train=False):
        nb_train_batches, train_loss = 0, 0.0
        self.model.train()
        with tqdm(total=len(train_data)) as pbar:
            for i, batch in enumerate(train_data):
                if len(batch) == 4:
                    data, target, locations, time_features = batch
                    locations = locations.to(self.device)
                    time_features = time_features.to(self.device)
                else:
                    data, target = batch[:2]
                    locations, time_features = None, None

                if pre_train:
                    data, target = data.to(self.device), target.to(self.device)
                    inputs, target_a, target_b, lam = mixup_data(data, target, alpha=1.0, use_cuda=True)
                    self.optimizer.zero_grad()
                    inputs, target_a, target_b = Variable(inputs), Variable(target_a), Variable(target_b)
                    target_a, target_b = torch.argmax(target_a, dim=1), torch.argmax(target_b, dim=1)
                    inputs = data.reshape(inputs.shape[0], -1, inputs.shape[1], inputs.shape[2])
                    outputs = self.model(inputs, locations, time_features)

                    loss_func = mixup_criterion(target_a, target_b, lam)
                    loss = loss_func(self.criterion, outputs)
                    loss.backward()
                    self.optimizer.step()
                else:
                    data, target = data.to(self.device), target.to(self.device)
                    self.optimizer.zero_grad()
                    data = data.reshape(data.shape[0], -1, data.shape[1], data.shape[2])
                    outputs = self.model(data, locations, time_features)
                    loss = self.criterion(outputs, target)
                    loss.backward()
                    self.optimizer.step()

                train_loss += loss.item()
                nb_train_batches += 1
                pbar.update(1)

            self.scheduler.step()
            train_loss /= nb_train_batches

        return train_loss

    def val_epoch(self, valid_data):
        self.model.eval()
        with torch.no_grad() and tqdm(total=len(valid_data)) as pbar:
            for i, batch in enumerate(valid_data):
                if len(batch) == 4:
                    data, target, locations, time_features = batch
                    locations = locations.to(self.device)
                    time_features = time_features.to(self.device)
                else:
                    data, target = batch[:2]
                    locations, time_features = None, None

                data, target = data.to(self.device), target.to(self.device)
                data = data.reshape(data.shape[0], -1, data.shape[1], data.shape[2])

                output = self.model(data, locations, time_features)
                output = torch.softmax(output, dim=1)
                output = torch.max(output, 1)[1]

                self.metric.update(output, target)
                pbar.update(1)

        score = self.metric.compute()
        return score


class ModelTester(object):
    def __init__(
            self,
            model: nn.Module,
            test_loader,
            device: str,
    ):
        self.device = device
        self.model = model.to(self.device)
        self.test_data = test_loader

    def predict(self):
        self.model.eval()
        output_arr = []
        print("== Testing start ==")
        with torch.no_grad() and tqdm(total=len(self.test_data)) as pbar:
            for i, batch in enumerate(self.test_data):
                if len(batch) == 4:
                    data, target, locations, time_features = batch
                    locations = locations.to(self.device)
                    time_features = time_features.to(self.device)
                else:
                    data, target = batch[:2]
                    locations, time_features = None, None

                data = data.reshape(data.shape[0], -1, data.shape[1], data.shape[2])
                data = data.to(self.device)

                output = self.model(data, locations, time_features)
                output = torch.softmax(output, dim=1)
                output = torch.max(output, 1)[1]
                output = output.cpu().numpy()
                output = output.reshape(-1)
                output_arr.append(output)
                pbar.update(1)

        output_arr = np.asarray(output_arr, dtype=np.int32)
        return output_arr
