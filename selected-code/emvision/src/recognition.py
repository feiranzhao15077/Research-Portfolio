# -*- coding: utf-8 -*-
"""微多普勒目标识别分类器（纯 NumPy 实现，无 sklearn/torch）。

- SoftmaxRegression：单层线性 softmax 回归（基线）；
- MLP：一层隐藏层（ReLU）+ softmax，手写前向/反向传播与
  批量梯度下降（可配动量），作为进阶模型；
- 混淆矩阵 / 准确率 / 学习曲线。

参照：姚璐（决策树+RBF-SVM 基线）、周玉军（CNN/RNN/Transformer 综述）——
我们先用可解释特征 + 自研浅层网络建立基线，后续可替换/对比深度学习模型。
"""

import numpy as np


def softmax(z):
    z = z - z.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return e / (e.sum(axis=-1, keepdims=True) + 1e-30)


def cross_entropy_loss(p, y):
    n = len(y)
    return -np.mean(np.log(p[np.arange(n), y] + 1e-30))


def accuracy(y_true, y_pred):
    return float(np.mean(np.asarray(y_true) == np.asarray(y_pred)))


def confusion_matrix(y_true, y_pred, n_classes):
    cm = np.zeros((n_classes, n_classes), dtype=int)
    for t, p in zip(y_true, y_pred):
        cm[t, p] += 1
    return cm


class SoftmaxRegression:
    """线性 softmax 回归（批量梯度下降）。"""

    def __init__(self, n_classes, lr=0.3, epochs=200, batch=32,
                 momentum=0.9, seed=0):
        self.n_classes = n_classes
        self.lr = lr
        self.epochs = epochs
        self.batch = batch
        self.momentum = momentum
        self.seed = seed
        self.loss_hist = []
        self.acc_hist = []

    def fit(self, X, y, Xv=None, yv=None):
        rng = np.random.default_rng(self.seed)
        n, d = X.shape
        k = self.n_classes
        self.W = rng.standard_normal((d, k)) * 0.01
        self.b = np.zeros(k)
        vW = np.zeros_like(self.W)
        vb = np.zeros_like(self.b)
        onehot = np.eye(k)[y]
        for ep in range(self.epochs):
            perm = rng.permutation(n)
            for s in range(0, n, self.batch):
                bi = perm[s:s + self.batch]
                Xb, yb, ob = X[bi], y[bi], onehot[bi]
                p = softmax(Xb @ self.W + self.b)
                gW = Xb.T @ (p - ob) / len(bi)
                gb = (p - ob).mean(axis=0)
                vW = self.momentum * vW - self.lr * gW
                vb = self.momentum * vb - self.lr * gb
                self.W += vW
                self.b += vb
            self.loss_hist.append(cross_entropy_loss(
                softmax(X @ self.W + self.b), y))
            if Xv is not None:
                self.acc_hist.append(accuracy(yv, self.predict(Xv)))
        return self

    def predict_proba(self, X):
        return softmax(X @ self.W + self.b)

    def predict(self, X):
        return np.argmax(self.predict_proba(X), axis=1)


class MLP:
    """两层 MLP：输入 → ReLU 隐藏层 → softmax 输出（手写反向传播）。"""

    def __init__(self, n_classes, hidden=32, lr=0.08, epochs=200,
                 batch=32, momentum=0.9, seed=0):
        self.n_classes = n_classes
        self.hidden = hidden
        self.lr = lr
        self.epochs = epochs
        self.batch = batch
        self.momentum = momentum
        self.seed = seed
        self.loss_hist = []
        self.acc_hist = []

    def fit(self, X, y, Xv=None, yv=None):
        rng = np.random.default_rng(self.seed)
        n, d = X.shape
        k = self.n_classes
        h = self.hidden
        # He 初始化
        self.W1 = rng.standard_normal((d, h)) * np.sqrt(2.0 / d)
        self.b1 = np.zeros(h)
        self.W2 = rng.standard_normal((h, k)) * np.sqrt(2.0 / h)
        self.b2 = np.zeros(k)
        v = {key: np.zeros_like(getattr(self, key))
             for key in ("W1", "b1", "W2", "b2")}
        onehot = np.eye(k)[y]
        for ep in range(self.epochs):
            perm = rng.permutation(n)
            for s in range(0, n, self.batch):
                bi = perm[s:s + self.batch]
                Xb, ob = X[bi], onehot[bi]
                z1 = Xb @ self.W1 + self.b1
                a1 = np.maximum(z1, 0.0)
                z2 = a1 @ self.W2 + self.b2
                p = softmax(z2)
                # 反向传播
                dz2 = (p - ob) / len(bi)
                gW2 = a1.T @ dz2
                gb2 = dz2.sum(axis=0)
                da1 = dz2 @ self.W2.T
                dz1 = da1 * (z1 > 0)
                gW1 = Xb.T @ dz1
                gb1 = dz1.sum(axis=0)
                # 动量更新
                for key, g in (("W1", gW1), ("b1", gb1),
                               ("W2", gW2), ("b2", gb2)):
                    v[key] = self.momentum * v[key] - self.lr * g
                    setattr(self, key, getattr(self, key) + v[key])
            self.loss_hist.append(cross_entropy_loss(
                self.predict_proba(X), y))
            if Xv is not None:
                self.acc_hist.append(accuracy(yv, self.predict(Xv)))
        return self

    def predict_proba(self, X):
        a1 = np.maximum(X @ self.W1 + self.b1, 0.0)
        return softmax(a1 @ self.W2 + self.b2)

    def predict(self, X):
        return np.argmax(self.predict_proba(X), axis=1)
