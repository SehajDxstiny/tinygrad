from tinygrad.nn import Conv2d,BatchNorm2d
from tinygrad.tensor import Tensor, Function
from tinygrad.nn import Conv2d,BatchNorm2d

# Model architecture from https://github.com/ultralytics/ultralytics/issues/189
  
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
  
# this function is from the original implementation
def autopad(k, p=None, d=1):  # kernel, padding, dilation
  """Pad to 'same' shape outputs."""
  if d > 1:
      k = d * (k - 1) + 1 if isinstance(k, int) else [d * (x - 1) + 1 for x in k]  # actual kernel-size
  if p is None:
      p = k // 2 if isinstance(k, int) else [x // 2 for x in k]  # auto-pad
  return p

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


# TODO: test this
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
    """
    Integral module of Distribution Focal Loss (DFL).
    Proposed in Generalized Focal Loss https://ieeexplore.ieee.org/document/9792391
    """
    def __init__(self, c1=16):
        """Initialize a convolutional layer with a given number of input channels."""
        self.conv = Conv2d(c1, 1, 1, bias=False).requires_grad(False)
        self.conv = Conv2d(c1, 1 , 1, bias=False)

        # x = torch.arange(c1, dtype=torch.float)
        # self.conv.weight.data[:] = nn.Parameter(x.view(1, c1, 1, 1))
        # self.c1 = c1

    def forward(self, x):
        """Applies a transformer layer on input tensor 'x' and returns a tensor."""
        b, c, a = x.shape  # batch, channels, anchors
        return self.conv(x.view(b, 4, self.c1, a).transpose(2, 1).softmax(1)).view(b, 4, a)
        # return self.conv(x.view(b, self.c1, 4, a).softmax(1)).view(b, 4, a)



#  ****** incomplete and probably doesn't work yet*******
# class Detect():
#     """YOLOv8 Detect head for detection models."""
  # dynamic = False  # force grid reconstruction
  # export = False  # export mode
  # shape = None
  # anchors = Tensor.empty(0)  # init
  # strides = Tensor.empty(0)  # init

#     def __init__(self, nc=80, ch=()):  # detection layer
#         super().__init__()
#         self.nc = nc  # number of classes
#         self.nl = len(ch)  # number of detection layers
#         self.reg_max = 16  # DFL channels (ch[0] // 16 to scale 4/8/12/16/20 for n/s/m/l/x)
#         self.no = nc + self.reg_max * 4  # number of outputs per anchor
#         self.stride = Tensor.zeros(self.nl)  # strides computed during build
#         c2, c3 = max((16, ch[0] // 4, self.reg_max * 4)), max(ch[0], self.nc)  # channels
#         self.cv2 = [Tensor.sequential([Conv_Block(x, c2, 3), Conv_Block(c2, c2, 3), Conv2d(c2, 4 * self.reg_max, 1)]) for x in ch]
#         self.cv3 = [Tensor.sequential([Conv_Block(x, c3, 3), Conv_Block(c3, c3, 3), Conv2d(c3, self.nc, 1)]) for x in ch]

#         TODO: a. DFL block create b. make_anchor create c. disttobox function 
#         # self.dfl = DFL(self.reg_max) if self.reg_max > 1 else nn.Identity()

    
