######################
# So you want to train a Neural CDE model?
# Let's get started!
######################
import torchcontroldiffeq
import math
import torch


######################
# A CDE model looks like
#
# z_t = z_0 + \int_0^t f_\theta(z_s) dX_s
#
# Where X is your data and f_\theta is a neural network. So the first thing we need to do is define such an f_\theta.
# That's what this CDEFunc class does.
# Here we've built a small single-hidden-layer neural network, whose hidden layer is of width 128.
######################
class CDEFunc(torch.nn.Module):
    def __init__(self, input_channels, hidden_channels):
        ######################
        # input_channels is the number of input channels in the data X. (Determined by the data.)
        # hidden_channels is the number of channels for z_t. (Determined by you!)
        ######################
        super(CDEFunc, self).__init__()
        self.input_channels = input_channels
        self.hidden_channels = hidden_channels

        self.linear1 = torch.nn.Linear(hidden_channels, 128)
        self.linear2 = torch.nn.Linear(128, input_channels * hidden_channels)

    def forward(self, z):
        z = self.linear1(z)
        z = torch.tanh(z)
        z = self.linear2(z)
        ######################
        # The one thing you need to be careful about is the shape of the output tensor. Ignoring the batch dimensions,
        # it must be a matrix, because we need it to represent a linear map from R^input_channels to
        # R^hidden_channels.
        ######################
        z = z.view(*z.shape[:-1], self.hidden_channels, self.input_channels)
        return z


######################
# Next, we need to package CDEFunc up into a model that computes the integral.
######################
class NeuralCDE(torch.nn.Module):
    def __init__(self, input_channels, hidden_channels, output_channels):
        super(NeuralCDE, self).__init__()
        self.hidden_channels = hidden_channels

        self.initial = torch.nn.Linear(input_channels, hidden_channels)
        self.func = CDEFunc(input_channels, hidden_channels)
        self.linear = torch.nn.Linear(hidden_channels, output_channels)

    def forward(self, coeffs, initial_t, final_t):
        ######################
        # Create the initial point as a function (here just linear) of the initial value.
        ######################
        cubic_spline = torchcontroldiffeq.NaturalCubicSpline(coeffs)
        z0 = self.initial(cubic_spline.evaluate(initial_t))

        ######################
        # Actually solve the CDE. z_T will be a tensor of shape (batch, sequence, channels). Here sequence=2, as that is
        # the length of its 't' argument.
        ######################
        z_T = torchcontroldiffeq.cdeint(X=cubic_spline,
                                        func=self.func,
                                        z0=z0,
                                        t=torch.cat([initial_t, final_t]))

        ######################
        # Both the initial value and the final value are returned from cdeint (this is consistent with how
        # torchdiffeq.odeint works). Extract just the final value, and then apply a linear map.
        ######################
        z_T = z_T[:, 1]
        pred_y = self.linear(z_T)
        return pred_y


######################
# Now we need some data.
# Here we have a simple example which generates some spirals, some going clockwise, some going anticlockwise.
######################
def get_data():
    linspace = torch.linspace(0., 4 * math.pi, 100)
    start = torch.rand(128) * 2 * math.pi
    times = start.unsqueeze(1) + linspace.unsqueeze(0)

    x_pos = torch.cos(times) / (1 + 0.5 * times)
    x_pos[:64] *= -1
    y_pos = torch.sin(times) / (1 + 0.5 * times)
    pos = torch.stack([x_pos, y_pos], dim=2)
    additive_noise = 0.06 * torch.rand(128, 100, 2) - 0.03
    X = pos + additive_noise
    y = torch.zeros(128)
    y[:64] = 1

    perm = torch.randperm(128)
    X = X[perm]
    y = y[perm]

    t = torch.linspace(0, 99, 100)

    ######################
    # t, X are treated as a time series. X has two channels, corresponding to the horizontal and vertical position of a
    # point in the spiral.
    # y are the labels, 0 or 1, corresponding to anticlockwise or clockwise respectively.
    ######################
    return t, X, y


def main():
    ######################
    # train_t is a one dimensional tensor of times, that must be shared across an entire batch during training (and so
    # for simplicity here we simply have the same times for the whole dataset). This means that train_t does not have a
    # batch dimension, and is just used everywhere we need the times.
    # Contrast both train_X and train_y, which have a batch dimension.
    ######################
    train_t, train_X, train_y = get_data()

    ######################
    # input_channels=2 because we have both the horizontal and vertical position of a point in the spiral.
    # hidden_channels=8 is the number of hidden channels for the evolving z_t, which we get to choose.
    # output_channels=1 because we're doing binary classification.
    ######################
    model = NeuralCDE(input_channels=2, hidden_channels=8, output_channels=1)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

    ######################
    # Now we turn our dataset into a continuous path. We do this here via natural cubic spline interpolation.
    # The resulting `train_coeffs` are some tensors describing the path.
    # For most problems, it's advisable to save these coeffs and treat them as a dataset, as this interpolation can take
    # a long time.
    ######################
    train_coeffs = torchcontroldiffeq.natural_cubic_spline_coeffs(train_t, train_X)

    train_dataset = torch.utils.data.TensorDataset(*train_coeffs, train_y)
    train_dataloader = torch.utils.data.DataLoader(train_dataset, batch_size=32)
    for epoch in range(100):
        for batch in train_dataloader:
            *batch_coeffs, batch_y = batch
            pred_y = model(batch_coeffs, train_t[0], train_t[-1]).squeeze(-1)
            loss = torch.nn.functional.binary_cross_entropy_with_logits(pred_y, batch_y)
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()
        print('Epoch: {}   Training loss: {}'.format(epoch, loss.item()))

    test_t, test_X, test_y = get_data()
    test_coeffs = torchcontroldiffeq.natural_cubic_spline_coeffs(test_t, test_X)
    pred_y = model(test_coeffs, test_t[0], test_t[-1]).squeeze(-1)
    binary_prediction = (torch.sigmoid(pred_y) > 0.5).to(test_y.dtype)
    prediction_matches = (binary_prediction == test_y).to(test_y.dtype)
    proportion_correct = prediction_matches.sum() / test_y.size(0)
    print('Test Accuracy: {}'.format(proportion_correct))


if __name__ == '__main__':
    main()
