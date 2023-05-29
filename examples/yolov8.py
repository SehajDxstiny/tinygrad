from tinygrad.nn import Conv2d,BatchNorm2d
from tinygrad.tensor import Tensor
from tinygrad.nn import Conv2d,BatchNorm2d
from tinygrad.helpers import dtypes, prod
import numpy as np
import math
# Model architecture from https://github.com/ultralytics/ultralytics/issues/189


# UTIL FUNCTIONS
def dist2bbox(distance, anchor_points, xywh=True, dim=-1):
  lt, rb = distance.chunk(2, dim)
  x1y1 = anchor_points - lt
  x2y2 = anchor_points + rb
  if xywh:
    c_xy = (x1y1 + x2y2) / 2
    wh = x2y2 - x1y1
    return c_xy.cat(wh, dim=1)  # xywh bbox
  return x1y1.cat(x2y2, dim=1) # xyxy bbox

def make_anchors(feats, strides, grid_cell_offset=0.5):
  anchor_points, stride_tensor = [], []
  assert feats is not None
  for i, stride in enumerate(strides):
    _, _, h, w = feats[i].shape
    sx = np.arange(w) + grid_cell_offset  # shift x
    sy = np.arange(h) + grid_cell_offset  # shift y
    sy, sx = np.meshgrid(sy, sx, indexing='ij')
    anchor_points.append(np.stack((sx, sy), -1).reshape(-1, 2))
    stride_tensor.append(np.full((h * w, 1), stride))
  return np.concatenate(anchor_points), np.concatenate(stride_tensor)

def clip_boxes(boxes, shape):
  if isinstance(boxes, Tensor):  # TODO: maybe tensor.clip can be used here.
   boxes[..., 0] = boxes[..., 0].maximum(0).minimum(shape[1])  # x1
   boxes[..., 1] = boxes[..., 1].maximum(0).minimum(shape[0])  # y1
   boxes[..., 2] = boxes[..., 2].maximum(0).minimum(shape[1])  # x2
   boxes[..., 3] = boxes[..., 3].maximum(0).minimum(shape[0])  # y2
  else:  # np.array 
    boxes[..., [0, 2]] = np.clip(boxes[..., [0, 2]], 0, shape[1])  # x1, x2
    boxes[..., [1, 3]] = np.clip(boxes[..., [1, 3]], 0, shape[0])  # y1, y2


def scale_boxes(img1_shape, boxes, img0_shape, ratio_pad=None):
  if ratio_pad is None:  # calculate from img0_shape
    gain = min(img1_shape[0] / img0_shape[0], img1_shape[1] / img0_shape[1])  # gain  = old / new
    pad = (img1_shape[1] - img0_shape[1] * gain) / 2, (img1_shape[0] - img0_shape[0] * gain) / 2  # wh padding
  else:
    gain = ratio_pad[0][0]
    pad = ratio_pad[1]

  boxes[..., [0, 2]] -= pad[0]  # x padding
  boxes[..., [1, 3]] -= pad[1]  # y padding
  boxes[..., :4] /= gain
  clip_boxes(boxes, img0_shape)
  return boxes

# TODO: remove clone 
def xywh2xyxy(x):
    y = x.clone() if isinstance(x, Tensor) else np.copy(x)
    y[..., 0] = x[..., 0] - x[..., 2] / 2  # top left x
    y[..., 1] = x[..., 1] - x[..., 3] / 2  # top left y
    y[..., 2] = x[..., 0] + x[..., 2] / 2  # bottom right x
    y[..., 3] = x[..., 1] + x[..., 3] / 2  # bottom right y
    return y

# TODO: use prod 
def box_iou(box1, box2):
    (a1, a2), (b1, b2) = box1[:, None].chunk(2, 2), box2.chunk(2, 1)
    intersection = (a2.minimum(b2) - a1.maximum(b1)).maximum(0).prod(2)
    # IoU = intersection / (area1 + area2 - intersection)
    box1 = box1.T
    box2 = box2.T
    area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
    area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
    return intersection / (area1[:, None] + area2 - intersection)

# this function is from the original implementation
def autopad(k, p=None, d=1):  # kernel, padding, dilation
  if d > 1:
    k = d * (k - 1) + 1 if isinstance(k, int) else [d * (x - 1) + 1 for x in k]  # actual kernel-size
  if p is None:
    p = k // 2 if isinstance(k, int) else [x // 2 for x in k]  # auto-pad
  return p
# MODULE Definitions
class SPPF:
  def __init__(self, c1, c2, k=5):
    c_ = c1 // 2  # hidden channels
    self.cv1 = Conv_Block(c1, c_, k, 1)
    self.cv2 = Conv_Block(c_ * 4, c2, k, 1)
    self.maxpool = lambda x : x.pad2d((k // 2, k // 2, k // 2, k // 2)).max_pool2d(kernel_size=5, stride=1)
        
  def forward(self, x):
    x = self.cv1(x)
    x2 = self.maxpool(x)
    x3 = self.maxpool(x2)
    x4 = self.maxpool(x3)
    return self.cv2(x.cat(x2, x3, x4, dim=1))

class Conv_Block:
  def __init__(self, c1, c2, kernel_size=1, stride=1, groups=1, dilation=1, padding=None):
    self.conv = Conv2d(c1,c2, kernel_size, stride, padding= autopad(kernel_size, padding, dilation),bias=False, groups=groups, dilation=dilation)
    self.batch = BatchNorm2d(c2)

  def __call__(self, x):
    return self.conv(x).silu()
  
  
class Bottleneck:
  def __init__(self, c1, c2 , shortcut: bool, g=1, kernels: list = (3,3), channel_factor=0.5):
    c_ = int(c2 * channel_factor)
    self.cv1 = Conv_Block(c1, c_, kernel_size=kernels[0], stride=1, padding=None)
    self.cv2 = Conv_Block(c_, c2, kernel_size=kernels[1], stride=1, padding=None, groups=g)
    self.residual = c1 == c2 and shortcut
    
  def forward(self, x):
    return x + self.cv2(self.cv1(x)) if self.residual else self.cv2(self.cv1(x))


class C2f:
  def __init__(self, c1, c2, n=1, shortcut=False, g=1, e=0.5):  # ch_in, ch_out, number, shortcut, groups, expansion
    self.c = int(c2 * e)  # hidden channels
    self.cv1 = Conv_Block(c1, 2 * self.c, 1, 1)
    self.cv2 = Conv_Block((2 + n) * self.c, c2, 1)  # optional act=FReLU(c2)
    self.bottleneck = [Bottleneck(self.c, self.c, shortcut, g, k=[(3, 3), (3, 3)], e=1.0) for _ in range(n)]

  def forward(self, x):
    y = self.cv1(x)
    # TODO: maybe can use 'Tensor.chunck' here
    y_chunks = [y[:, :self.c], y[:, self.c:]]
    y2 = [m(y_chunks[-1]) for m in self.bottleneck]
    concatenated = tuple([y] + y_chunks + y2)
    return self.cv2(concatenated)

class DFL():
  def __init__(self, c1=16):
    self.conv = Conv2d(c1, 1, 1, bias=False)
    x = Tensor.arange(c1, dtypes.float32)
    self.conv.weight = x.reshape(1, c1, 1, 1)
    self.c1 = c1

  def forward(self, x):
    b, c, a = x.shape # batch, channels, anchors
    return self.conv(x.reshape(b, 4, self.c1, a).transpose(2, 1).softmax(1)).reshape(b, 4, a)

  
# incomplete and untested
class DetectionHead():
  anchors = Tensor.empty(0)
  strides = Tensor.empty(0)

  def __init__(self, nc=80, filters=()):
    super().__init__()
    self.ch = 16  # DFL channels
    self.nc = nc  # number of classes
    self.nl = len(filters)  # number of detection layers
    self.no = nc + self.ch * 4  # number of outputs per anchor
    self.stride = Tensor.zeros(self.nl)  # strides computed during build #TODO - figure this out

    c1 = math.max(filters[0], self.nc)
    c2 = math.max((filters[0] // 4, self.ch * 4))

    self.dfl = DFL(self.ch) 
    self.cls = [[Conv_Block(x, c1, 3), Conv_Block(c1, c1, 3), Conv2d(c1, self.nc, 1)] for x in filters]
    self.box = [[Conv_Block(x, c2, 3), Conv_Block(c2, c2, 3), Conv2d(c2, 4 * self.ch, 1)] for x in filters]
    
  def forward(self, x):
    for i in range(self.nl):
      x[i] = x[i].sequential(self.box[i]).cat(x[i].sequential(self.cls[i]))
    self.anchors, self.strides = (x.transpose(0, 1) for x in make_anchors(x, self.stride, 0.5))
    x = Tensor.stack([i.view(x[0].shape[0], self.no, -1) for i in x], dim=2)
    box, cls = x.split((self.ch * 4, self.nc), 1)
    a, b = self.dfl(box).chunk(2,1)
    a = self.anchors.unsqueeze(0) - a
    b = self.anchors.unsqueeze(0) + b
    box = Tensor.stack(((a + b) / 2, b - a), dim=1)
    return Tensor.stack((box * self.strides, cls.sigmoid()), dim=1)
  
  def bias_init(self):
      """Initialize Detect() biases, WARNING: requires stride availability."""
      m = self  # self.model[-1]  # Detect() module
      # cf = torch.bincount(torch.tensor(np.concatenate(dataset.labels, 0)[:, 0]).long(), minlength=nc) + 1
      # ncf = math.log(0.6 / (m.nc - 0.999999)) if cf is None else torch.log(cf / cf.sum())  # nominal class frequency
      for a, b, s in zip(m.cv2, m.cv3, m.stride):  # from
          a[-1].bias.data[:] = 1.0  # box
          b[-1].bias.data[:m.nc] = math.log(5 / m.nc / (640 / s) ** 2)  # cls (.01 objects, 80 classes, 640 img)



# (a. non max suppression b. scale boxes (done) c. clip boxes (done) d. xywh2xyxy e. box_iou - ops.py) (c. Result d. predict.cli e. init - basepredictor)


  
    


