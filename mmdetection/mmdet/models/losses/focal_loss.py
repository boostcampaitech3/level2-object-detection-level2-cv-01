import torch
import torch.nn as nn
import torch.nn.functional as F
from mmcv.ops import sigmoid_focal_loss as _sigmoid_focal_loss

from ..builder import LOSSES
from .utils import weight_reduce_loss


# This method is only for debugging
def py_sigmoid_focal_loss(pred,
                          target,
                          weight=None,
                          gamma=2.0,
                          alpha=0.25,
                          reduction='mean',
                          avg_factor=None):
    """PyTorch version of `Focal Loss <https://arxiv.org/abs/1708.02002>`_.
    Args:
        pred (torch.Tensor): The prediction with shape (N, C), C is the
            number of classes
        target (torch.Tensor): The learning label of the prediction.
        weight (torch.Tensor, optional): Sample-wise loss weight.
        gamma (float, optional): The gamma for calculating the modulating
            factor. Defaults to 2.0.
        alpha (float, optional): A balanced form for Focal Loss.
            Defaults to 0.25.
        reduction (str, optional): The method used to reduce the loss into
            a scalar. Defaults to 'mean'.
        avg_factor (int, optional): Average factor that is used to average
            the loss. Defaults to None.
    """
    pred_sigmoid = pred.sigmoid()
    target = target.type_as(pred)
    pt = (1 - pred_sigmoid) * target + pred_sigmoid * (1 - target)
    focal_weight = (alpha * target + (1 - alpha) *
                    (1 - target)) * pt.pow(gamma)
    loss = F.binary_cross_entropy_with_logits(
        pred, target, reduction='none') * focal_weight
    if weight is not None:
        if weight.shape != loss.shape:
            if weight.size(0) == loss.size(0):
                # For most cases, weight is of shape (num_priors, ),
                #  which means it does not have the second axis num_class
                weight = weight.view(-1, 1)
            else:
                # Sometimes, weight per anchor per class is also needed. e.g.
                #  in FSAF. But it may be flattened of shape
                #  (num_priors x num_class, ), while loss is still of shape
                #  (num_priors, num_class).
                assert weight.numel() == loss.numel()
                weight = weight.view(loss.size(0), -1)
        assert weight.ndim == loss.ndim
    loss = weight_reduce_loss(loss, weight, reduction, avg_factor)
    return loss


def sigmoid_focal_loss(pred,
                       target,
                       weight=None,
                       gamma=2.0,
                       alpha=0.25,
                       reduction='mean',
                       avg_factor=None):
    r"""A warpper of cuda version `Focal Loss
    <https://arxiv.org/abs/1708.02002>`_.
    Args:
        pred (torch.Tensor): The prediction with shape (N, C), C is the number
            of classes.
        target (torch.Tensor): The learning label of the prediction.
        weight (torch.Tensor, optional): Sample-wise loss weight.
        gamma (float, optional): The gamma for calculating the modulating
            factor. Defaults to 2.0.
        alpha (float, optional): A balanced form for Focal Loss.
            Defaults to 0.25.
        reduction (str, optional): The method used to reduce the loss into
            a scalar. Defaults to 'mean'. Options are "none", "mean" and "sum".
        avg_factor (int, optional): Average factor that is used to average
            the loss. Defaults to None.
    """
    # Function.apply does not accept keyword arguments, so the decorator
    # "weighted_loss" is not applicable
    loss = _sigmoid_focal_loss(pred.contiguous(), target, gamma, alpha, None,
                               'none')
    if weight is not None:
        if weight.shape != loss.shape:
            if weight.size(0) == loss.size(0):
                # For most cases, weight is of shape (num_priors, ),
                #  which means it does not have the second axis num_class
                weight = weight.view(-1, 1)
            else:
                # Sometimes, weight per anchor per class is also needed. e.g.
                #  in FSAF. But it may be flattened of shape
                #  (num_priors x num_class, ), while loss is still of shape
                #  (num_priors, num_class).
                assert weight.numel() == loss.numel()
                weight = weight.view(loss.size(0), -1)
        assert weight.ndim == loss.ndim
    loss = weight_reduce_loss(loss, weight, reduction, avg_factor)
    return loss


@LOSSES.register_module()
class FocalLoss(nn.Module):

    def __init__(self,
                 use_sigmoid=True,
                 gamma=2.0,
                 alpha=0.25,
                 reduction='mean',
                 loss_weight=1.0):
        """`Focal Loss <https://arxiv.org/abs/1708.02002>`_
        Args:
            use_sigmoid (bool, optional): Whether to the prediction is
                used for sigmoid or softmax. Defaults to True.
            gamma (float, optional): The gamma for calculating the modulating
                factor. Defaults to 2.0.
            alpha (float, optional): A balanced form for Focal Loss.
                Defaults to 0.25.
            reduction (str, optional): The method used to reduce the loss into
                a scalar. Defaults to 'mean'. Options are "none", "mean" and
                "sum".
            loss_weight (float, optional): Weight of loss. Defaults to 1.0.
        """
        super(FocalLoss, self).__init__()
        assert use_sigmoid is True, 'Only sigmoid focal loss supported now.'
        self.use_sigmoid = use_sigmoid
        self.gamma = gamma
        self.alpha = alpha
        self.reduction = reduction
        self.loss_weight = loss_weight

    def forward(self,
                pred,
                target,
                weight=None,
                avg_factor=None,
                reduction_override=None):
        """Forward function.
        Args:
            pred (torch.Tensor): The prediction.
            target (torch.Tensor): The learning label of the prediction.
            weight (torch.Tensor, optional): The weight of loss for each
                prediction. Defaults to None.
            avg_factor (int, optional): Average factor that is used to average
                the loss. Defaults to None.
            reduction_override (str, optional): The reduction method used to
                override the original reduction method of the loss.
                Options are "none", "mean" and "sum".
        Returns:
            torch.Tensor: The calculated loss
        """
        assert reduction_override in (None, 'none', 'mean', 'sum')
        reduction = (
            reduction_override if reduction_override else self.reduction)
        if self.use_sigmoid:
            if torch.cuda.is_available() and pred.is_cuda:
                calculate_loss_func = sigmoid_focal_loss
            else:
                num_classes = pred.size(1)
                target = F.one_hot(target, num_classes=num_classes + 1)
                target = target[:, :num_classes]
                calculate_loss_func = py_sigmoid_focal_loss

            loss_cls = self.loss_weight * calculate_loss_func(
                pred,
                target,
                weight,
                gamma=self.gamma,
                alpha=self.alpha,
                reduction=reduction,
                avg_factor=avg_factor)

        else:
            raise NotImplementedError
        return loss_cls


@LOSSES.register_module()
class BinaryFocalLoss(nn.Module):

    def __init__(self,
                 alpha=0.25,
                 beta=4.,
                 gamma=2.,
                 weight=1,
                 sigmoid_clamp=1e-4,
                 ignore_high_fp=-1.,
                 reduction='mean'):
        """A Gaussian heatmap focal loss calculation, it use a small portion of
        points as positive sample points.
            `Probabilistic two-stage detection
            <https://arxiv.org/abs/2103.07461>`_
            `Focal Loss
            <https://arxiv.org/abs/1708.02002>`_
        Args:
            alpha (float, optional): A balanced form for Focal Loss.
                Defaults to 0.25.
            beta (float, optional): The beta for calculating the negative
                sample points loss modulating factor. Defaults to 4.0.
            gamma (float, optional): he gamma for calculating the positive
                sample points loss modulating factor. Defaults to 2.0.
            weight (float, optional): Weight of heatmap loss.
            sigmoid_clamp (float, optional): A value used to determine
                clamp range.
            ignore_high_fp (float, optional): A threshold to ignore sample
                points with high positive scores when calculating negative
                loss.
            reduction (string, optional): The method used to reduce the
                loss into a scalar. Defaults to 'sum' for heatmap loss.
        """
        super(BinaryFocalLoss, self).__init__()
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.weight = weight
        self.sigmoid_clamp = sigmoid_clamp
        self.ignore_high_fp = ignore_high_fp
        self.reduction = reduction

    def forward(self,
                inputs,
                targets,
                pos_inds,
                pos_weight=None,
                neg_weight=None,
                avg_factor=None,
                reduction_override=None):
        """
        Args:
            inputs (torch.Tensor): Flattened heatmap prediction.
            targets (torch.Tensor): Flattened target heatmap.
            pos_inds (torch.Tensor): Indices of positive sample points.
            pos_weight(torch.Tensor, optional): The element wise  weight of
                positive loss. Defaults to None.
            neg_weight(torch.Tensor, optional): The element wise  weight of
                negative loss. Defaults to None.
            avg_factor (torch.Tensor): Average factor that is used to
                average the loss. Defaults to None.
            reduction_override (string): Override reduction.
        Returns:
            Loss tensor with the reduction option applied.
        """

        assert reduction_override in (None, 'none', 'mean', 'sum')
        reduction = (
            reduction_override if reduction_override else self.reduction)
        pred = torch.clamp(
            inputs.sigmoid_(),
            min=self.sigmoid_clamp,
            max=1 - self.sigmoid_clamp)
        neg_weights = torch.pow(1 - targets, self.beta)
        pos_pred = pred[pos_inds]
        pos_loss = torch.log(pos_pred) * \
            torch.pow(1 - pos_pred, self.gamma)
        neg_loss = torch.log(1 - pred) * \
            torch.pow(pred, self.gamma) * neg_weights

        if self.ignore_high_fp > 0:
            not_high_fp = (pred < self.ignore_high_fp).float()
            neg_loss = not_high_fp * neg_loss

        if self.alpha >= 0:
            pos_loss = -self.alpha * pos_loss
            neg_loss = -(1 - self.alpha) * neg_loss

        pos_loss = weight_reduce_loss(pos_loss, pos_weight, reduction,
                                      avg_factor)
        neg_loss = weight_reduce_loss(neg_loss, neg_weight, reduction,
                                      avg_factor)

        return self.weight * pos_loss, self.weight * neg_loss