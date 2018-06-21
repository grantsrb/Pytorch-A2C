import torch
from torch.autograd import Variable
import torch.nn as nn
import torch.nn.functional as F
from utils import cuda_if, discount


class Updater():
    """
    This class converts the data collected from the rollouts into useable data to update
    the model. The main function to use is calc_loss which accepts a rollout to
    add to the global loss of the model. The model isn't updated, however, until calling
    calc_gradients followed by update_model. If the size of the epoch is restricted by the memory, you can call calc_gradients to clear the graph.
    """

    def __init__(self, net, hyps): 
        self.net = net 
        self.hyps = hyps
        self.optim = self.new_optim(hyps['lr'])    
        self.info = {}
        self.norm = 0

    def update_model(self, shared_data):
        """
        This function accepts the data collected from a rollout and performs Q value update iterations
        on the neural net.

        shared_data - dict of torch tensors with shared memory to collect data. Each 
                tensor contains indices from idx*n_tsteps to (idx+1)*n_tsteps
                Keys (assume string keys):
                    "states" - MDP states at each timestep t
                            type: FloatTensor
                            shape: (n_states, *state_shape)
                    "next_states" - MDP states at timestep t+1
                            type: FloatTensor
                            shape: (n_states, *state_shape)
                    "rewards" - Collects float rewards collected at each timestep t
                            type: FloatTensor
                            shape: (n_states,)
                    "dones" - Collects the dones collected at each timestep t
                            type: FloatTensor
                            shape: (n_states,)
                    "actions" - Collects actions performed at each timestep t
                            type: LongTensor
                            shape: (n_states,)
        """
        hyps = self.hyps
        net = self.net

        states = shared_data['states']
        next_states = shared_data['next_states']
        rewards = shared_data['rewards']
        dones = shared_data['dones']
        actions = shared_data['actions']

        # Forward Pass
        vals, logits = net(Variable(cuda_if(states)))
        next_vals, _ = net(Variable(cuda_if(next_states)))

        # Log Probabilities
        log_softs = F.log_softmax(logits, dim=-1)
        logprobs = log_softs[torch.arange(len(actions)).long(), actions]

        # Advantages
        advs = self.gae(rewards.squeeze(), vals.data.squeeze(), next_vals.data.squeeze(), dones.squeeze(), hyps['gamma'], hyps['lambda_'])
        advs = (advs - advs.mean()) / (advs.std() + 1e-6)

        # Returns
        if hyps['use_nstep_rets']: 
            returns = advantages + vals.data.squeeze()
        else: 
            returns = cuda_if(discount(rewards.squeeze(), dones.squeeze(), hyps['gamma']))
        
        # A2C Losses
        pi_loss = -(logprobs.squeeze()*Variable(advs.squeeze())).mean()
        val_loss = hyps['val_coef']*F.mse_loss(vals.squeeze(), returns)
        entr_loss = -hyps['entr_coef']*(log_softs*F.softmax(logits, dim=-1)).sum(-1).mean()

        loss = pi_loss + val_loss - entr_loss
        loss.backward()
        self.norm = nn.utils.clip_grad_norm_(net.parameters(), hyps['max_norm'])
        optimizer.step()
        optimizer.zero_grad()

        self.info = {"Loss":loss.item(), "Pi_Loss":pi_loss.item(), 
                    "ValLoss":val_loss.item(), "Entropy":entr_loss.item(),
                    "GradNorm":norm.item()}
        return self.info

    def gae(self, rewards, values, next_vals, dones, gamma, lambda_):
        """
        Performs Generalized Advantage Estimation
    
        rewards - torch FloatTensor of actual rewards collected. Size = L
        values - torch FloatTensor of value predictions. Size = L
        next_vals - torch FloatTensor of value predictions. Size = L
        dones - torch FloatTensor of done signals. Size = L
        gamma - float discount factor
        lambda_ - float gae moving average factor
    
        Returns
         advantages - torch FloatTensor of genralized advantage estimations. Size = L
        """
    
        deltas = rewards + gamma*next_vals*(1-dones) - values
        return cuda_if(discount(deltas, dones, gamma*lambda_))

    def print_statistics(self):
        print(" – ".join([key+": "+str(round(val,5)) for key,val in sorted(self.info.items())]))

    def log_statistics(self, log, T, reward, avg_action, best_avg_rew):
        log.write("Step:"+str(T)+" – "+" – ".join([key+": "+str(round(val,5)) if "ntropy" not in key else key+": "+str(val) for key,val in self.info.items()]+["EpRew: "+str(reward), "AvgAction: "+str(avg_action), "BestRew:"+str(best_avg_rew)]) + '\n')
        log.flush()

    def save_model(self, net_file_name, optim_file_name):
        """
        Saves the state dict of the model to file.

        file_name - string name of the file to save the state_dict to
        """
        torch.save(self.net.state_dict(), net_file_name)
        if optim_file_name is not None:
            torch.save(self.optim.state_dict(), optim_file_name)
    
    def new_lr(self, new_lr):
        new_optim = self.new_optim(new_lr)
        new_optim.load_state_dict(self.optim.state_dict())
        self.optim = new_optim

    def new_optim(self, lr):
        if self.hyps['optim_type'] == 'rmsprop':
            new_optim = optim.RMSprop(self.net.parameters(), lr=lr) 
        elif self.hyps['optim_type'] == 'adam':
            new_optim = optim.Adam(self.net.parameters(), lr=lr) 
        else:
            new_optim = optim.RMSprop(self.net.parameters(), lr=lr) 
        return new_optim

