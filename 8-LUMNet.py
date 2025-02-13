import timm
import torch
import torch.nn as nn
import torch.nn.functional as F

class _DenseASPPConv(nn.Sequential):
    def __init__(self, in_channels, inter_channels, out_channels, atrous_rate,
                 drop_rate=0.1, norm_layer=nn.BatchNorm2d, norm_kwargs=None):
        super(_DenseASPPConv, self).__init__()
        self.add_module('conv1', nn.Conv2d(in_channels, inter_channels, 1)),
        self.add_module('bn1', norm_layer(inter_channels, **({} if norm_kwargs is None else norm_kwargs))),
        self.add_module('relu1', nn.ReLU(True)),
        self.add_module('conv2', nn.Conv2d(inter_channels, out_channels, 3, dilation=atrous_rate, padding=atrous_rate)),
        self.add_module('bn2', norm_layer(out_channels, **({} if norm_kwargs is None else norm_kwargs))),
        self.add_module('relu2', nn.ReLU(True)),
        self.drop_rate = drop_rate

    def forward(self, x):
        features = super(_DenseASPPConv, self).forward(x)
        if self.drop_rate > 0:
            features = F.dropout(features, p=self.drop_rate, training=self.training)
        return features


class DenseASPPBlock(nn.Module):
    def __init__(self, in_channels, inter_channels1, inter_channels2,
                 norm_layer=nn.BatchNorm2d, norm_kwargs=None):
        super(DenseASPPBlock, self).__init__()
        self.aspp_3 = _DenseASPPConv(in_channels, inter_channels1, inter_channels2, 3, 0.1,
                                     norm_layer, norm_kwargs)
        self.aspp_6 = _DenseASPPConv(in_channels + inter_channels2 * 1, inter_channels1, inter_channels2, 6, 0.1,
                                     norm_layer, norm_kwargs)
        self.aspp_12 = _DenseASPPConv(in_channels + inter_channels2 * 2, inter_channels1, inter_channels2, 12, 0.1,
                                      norm_layer, norm_kwargs)
        self.aspp_18 = _DenseASPPConv(in_channels + inter_channels2 * 3, inter_channels1, inter_channels2, 18, 0.1,
                                      norm_layer, norm_kwargs)
        self.aspp_24 = _DenseASPPConv(in_channels + inter_channels2 * 4, inter_channels1, inter_channels2, 24, 0.1,
                                      norm_layer, norm_kwargs)
        self.conv11 = nn.Conv2d(in_channels=in_channels+inter_channels1*5,out_channels=in_channels,kernel_size=1,stride=1)

    def forward(self, x):
        aspp3 = self.aspp_3(x)
        x = torch.cat([aspp3, x], dim=1)
        aspp6 = self.aspp_6(x)
        x = torch.cat([aspp6, x], dim=1)
        aspp12 = self.aspp_12(x)
        x = torch.cat([aspp12, x], dim=1)
        aspp18 = self.aspp_18(x)
        x = torch.cat([aspp18, x], dim=1)
        aspp24 = self.aspp_24(x)
        x = torch.cat([aspp24, x], dim=1)
        x=self.conv11(x)
        return x

class ChannelAttentionModule(nn.Module):
    def __init__(self, channel, ratio=16):
        super(ChannelAttentionModule, self).__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.max_pool = nn.AdaptiveMaxPool2d(1)

        self.shared_MLP = nn.Sequential(
            nn.Conv2d(channel, channel // ratio, 1, bias=False),
            nn.ReLU(),
            nn.Conv2d(channel // ratio, channel, 1, bias=False)
        )
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        avgout = self.shared_MLP(self.avg_pool(x))
        # print(avgout.shape)
        maxout = self.shared_MLP(self.max_pool(x))
        return self.sigmoid(avgout + maxout)


class SpatialAttentionModule(nn.Module):
    def __init__(self):
        super(SpatialAttentionModule, self).__init__()
        self.conv2d = nn.Conv2d(in_channels=2, out_channels=1, kernel_size=7, stride=1, padding=3)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        avgout = torch.mean(x, dim=1, keepdim=True)
        maxout, _ = torch.max(x, dim=1, keepdim=True)
        out = torch.cat([avgout, maxout], dim=1)
        out = self.sigmoid(self.conv2d(out))
        return out

class unetUp(nn.Module):
    def __init__(self, in_size, out_size):
        super(unetUp, self).__init__()
        self.conv1  = nn.Conv2d(in_size, out_size, kernel_size = 3, padding = 1)
        self.bn1 = nn.BatchNorm2d(out_size)
        self.conv2  = nn.Conv2d(out_size, out_size, kernel_size = 3, padding = 1)
        self.bn2 = nn.BatchNorm2d(out_size)
        self.up     = nn.UpsamplingBilinear2d(scale_factor = 2)
        self.relu   = nn.ReLU()
    def forward(self, input1):
        output = self.bn1(self.conv1(input1))
        output = self.relu(output)
        output = self.bn2(self.conv2(output))
        output = self.relu(output)
        output = self.up(output)
        return output

class LUMNet(nn.Module):
    def __init__(self, pretrained = False, backbone = 'vgg16_bn'):
        super(LUMNet, self).__init__()
        self.backbone = timm.create_model("vgg16_bn", features_only=True,
                             out_indices=(0,1, 2, 3, 4), pretrained=True)
        filters  = [64, 128, 256, 512,512]
        self.aspp=DenseASPPBlock(512,128,128)

        self.unetUp5=unetUp(filters[4],filters[3])
        self.unetUp4=unetUp(filters[4]+filters[3],filters[3])
        self.unetUp3=unetUp(filters[3]+filters[2],filters[2])
        self.unetUp2=unetUp(filters[2]+filters[1],filters[1])

        self.maxpool2 = nn.MaxPool2d(kernel_size=2)
        self.maxpool3 = nn.MaxPool2d(kernel_size=4)
        self.maxpool4 = nn.MaxPool2d(kernel_size=8)

        self.ATTConv1 = nn.Conv2d(in_channels=1, out_channels=64, kernel_size=3, stride=1, padding=1)
        self.ATTConv2 = nn.Conv2d(in_channels=1, out_channels=128, kernel_size=3, stride=1, padding=1)
        self.ATTConv3 = nn.Conv2d(in_channels=1, out_channels=256, kernel_size=3, stride=1, padding=1)
        self.ATTConv4 = nn.Conv2d(in_channels=1, out_channels=512, kernel_size=3, stride=1, padding=1)

        self.CAM1=ChannelAttentionModule(64)
        self.CAM2=ChannelAttentionModule(128)
        self.CAM3=ChannelAttentionModule(256)
        self.CAM4=ChannelAttentionModule(512)

        self.SAM1=SpatialAttentionModule()
        self.SAM2=SpatialAttentionModule()
        self.SAM3=SpatialAttentionModule()
        self.SAM4=SpatialAttentionModule()

        self.rg = nn.Conv2d(in_channels=192, out_channels=1, kernel_size=1, stride=1, padding=0)
    def forward(self, input1,input2):
        in1_feat1,in1_feat2, in1_feat3, in1_feat4, in1_feat5 = self.backbone(input1)
        in2_max2=self.maxpool2(input2)
        in2_max3=self.maxpool3(input2)
        in2_max4=self.maxpool4(input2)

        in1_feat1=in1_feat1

        in2_max1=self.ATTConv1(input2)
        in2_max2=self.ATTConv2(in2_max2)
        in2_max3=self.ATTConv3(in2_max3)
        in2_max4=self.ATTConv4(in2_max4)

        CAM1=self.CAM1(in2_max1)
        CAM2=self.CAM2(in2_max2)
        CAM3=self.CAM3(in2_max3)
        CAM4=self.CAM4(in2_max4)

        SAM1=self.SAM1(in2_max1)
        SAM2=self.SAM2(in2_max2)
        SAM3=self.SAM3(in2_max3)
        SAM4=self.SAM4(in2_max4)

        in1_feat1=in1_feat1*CAM1*SAM1
        in1_feat2=in1_feat2*CAM2*SAM2
        in1_feat3=in1_feat3*CAM3*SAM3
        in1_feat4=in1_feat4*CAM4*SAM4

        in1_feat5=self.aspp(in1_feat5)

        de1_feat5 = self.unetUp5(in1_feat5)

        temp = torch.cat((in1_feat4, de1_feat5), dim=1)
        de1_feat4 = self.unetUp4(temp)

        temp = torch.cat((in1_feat3, de1_feat4), dim=1)
        de1_feat3 = self.unetUp3(temp)

        temp = torch.cat((in1_feat2, de1_feat3), dim=1)
        de1_feat2 = self.unetUp2(temp)

        temp = torch.cat((in1_feat1, de1_feat2), dim=1)

        output=self.rg(temp)
        return output



if __name__ == '__main__':
    model=LUMNet()
    input1=torch.randn((4,3,512,512))
    input2=torch.randn((4,1,512,512))
    output=model(input1,input2)
    print(output.shape)