from utills.rl_utills import *

class Actor(nn.Module):
    def __init__(self, input_dim, output_dim, args):
        super().__init__()
        hidden_size = args.Model.hidden_size
        self.a_net = mlp(input_dim, hidden_size, output_dim, len(hidden_size)+1, nn.Tanh)
        self.gpu = args.Option.gpu

    def forward(self, x):
        # input -> output(mean of torque+std(constant))
        mu = self.a_net(x)
        log_std = torch.zeros_like(mu)
        std = torch.exp(log_std)

        return mu, std

    def get_action(self, mu, std):
        normal = Normal(mu, std)
        action = normal.sample()
        if self.gpu:
            return action.data.cpu().numpy()
        else:
            return action.data.numpy()

    def get_log_prob(self, actions, mu, std):
        normal = Normal(mu, std)
        log_prob = normal.log_prob(actions) #log_probability of policy
        return log_prob

class Critic(nn.Module):
    def __init__(self, input_dim, args):
        super().__init__()
        hidden_size = args.Model.hidden_size
        self.c_net = mlp(input_dim, hidden_size, 1, len(hidden_size)+1, nn.Tanh)


    def forward(self, x):
        return self.c_net(x)
