import socket
import threading
import logging
from p2pfl.command import *
from p2pfl.communication_protocol import CommunicationProtocol
from p2pfl.encrypter import Encrypter
from p2pfl.settings import Settings
from p2pfl.utils.observer import Events, Observable

########################
#    NodeConnection    #
########################

# organizar algo código

# dividirlo en NC connection base y en nc connection  ???

class NodeConnection(threading.Thread, Observable):
    """
    This class represents a connection to a node. It is a thread, so it's going to process all messages in a background thread using the CommunicationProtocol.

    The NodeConnection can recive many messages in a single recv and exists 2 kinds of messages:
        - Binary messages (models)
        - Text messages (commands)

    Carefully, if the connection is broken, it will be closed. If the user wants to reconnect, he/she should create a new connection.

    Args:
        parent_node: The parent node of this connection.
        s: The socket of the connection.
        addr: The address of the node that is connected to.
    """

    def __init__(self, parent_node_name, s, addr, aes_cipher):
        threading.Thread.__init__(self)
        self.name = "node_connection-" + parent_node_name + "-" + str(addr[0]) + ":" + str(addr[1])
        Observable.__init__(self)
        self.terminate_flag = threading.Event()
        self.socket = s
        self.socket_lock = threading.Lock()
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.addr = addr
        self.train_num_samples = 0
        self.test_num_samples = 0
        self.param_bufffer = b""
        
        self.model_ready = -1

        self.aes_cipher = aes_cipher

        self.model_initialized = False

        self.train_set_votes = {}

        self.models_agregated = []


        self.comm_protocol = CommunicationProtocol({
            CommunicationProtocol.BEAT: Beat_cmd(self),
            CommunicationProtocol.STOP: Stop_cmd(self),
            CommunicationProtocol.CONN_TO: Conn_to_cmd(self),
            CommunicationProtocol.START_LEARNING: Start_learning_cmd(self),
            CommunicationProtocol.STOP_LEARNING: Stop_learning_cmd(self),
            CommunicationProtocol.NUM_SAMPLES: Num_samples_cmd(self),
            CommunicationProtocol.PARAMS: Params_cmd(self),
            CommunicationProtocol.MODELS_READY: Models_Ready_cmd(self),
            CommunicationProtocol.METRICS: Metrics_cmd(self),
            CommunicationProtocol.VOTE_TRAIN_SET: Vote_train_set_cmd(self),
            CommunicationProtocol.MODELS_AGREGATED: Models_agregated_cmd(self),
            CommunicationProtocol.MODEL_INITIALIZED: Model_initialized_cmd(self),
        })

    def add_processed_messages(self,msgs):
        """
        Add to a list of messages that have been processed. (By other nodes)

        Args:
            msgs: The list of messages that have been processed.

        """
        self.comm_protocol.add_processed_messages(msgs)

    def get_addr(self):
        """
        Returns:
            The address of the node that is connected to.   
        """
        return self.addr

    def get_name(self):
        """
        Returns:
            The name of the connection.
        """
        return self.addr[0] + ":" + str(self.addr[1])

    ###############
    # Model Ready #
    ###############

    def set_model_ready_status(self,round):
        """
        Set the last ready round of the other node.

        Args:
            round: The last ready round of the other node.
        """
        self.model_ready = round

    def get_ready_model_status(self):
        """
        Returns:
            The last ready round of the other node.
        """
        return self.model_ready
    
    #####################
    # Model Initialized #
    #####################

    def set_model_initialized(self, value):
        """
        Set the model initialized.
        """
        self.model_initialized = value

    def get_model_initialized(self):
        """
        Returns:
            The model initialized.
        """
        return self.model_initialized

    ####################
    # Models Agregated #
    ####################

    def set_models_agregated(self,models):
        """
        Set the models agregated.
        
        Args:
            models: The models agregated.
        """
        self.models_agregated = models

    def get_models_agregated(self):
        """
        Returns:
            The models agregated.
        """
        return self.models_agregated

    def set_num_samples(self,train,test):
        """
        Indicates the number of samples of the otrh node.
         
        Args:
            num: The number of samples of the other node.
        """
        self.train_num_samples = train
        self.test_num_samples = test

    def get_num_samples(self):
        return self.train_num_samples, self.test_num_samples

    def add_param_segment(self,data):
        """
        Add a segment of parameters to the buffer.
        
        Args:
            data: The segment of parameters.
        """
        self.param_bufffer = self.param_bufffer + data

    def get_params(self):
        """
        Returns:
            The parameters buffer content.
        """
        return self.param_bufffer

    def clear_buffer(self):
        """
        Clear the params buffer.
        """
        self.param_bufffer = b""

    ################### 
    #    Main Loop    # 
    ###################

    def start(self, force=False):
        self.notify(Events.NODE_CONNECTED_EVENT, (self,force))
        return super().start()


    def run(self):
        """
        NodeConnection loop. Recive and process messages.
        """
        self.socket.settimeout(Settings.NODE_TIMEOUT)
        overflow = 0
        buffer = b""
        while not self.terminate_flag.is_set():
            try:
                # Recive message
                og_msg = b""
                if overflow == 0:
                    og_msg = self.socket.recv(Settings.BLOCK_SIZE)
                else:
                    og_msg = buffer + self.socket.recv(overflow) #alinear el colapso
                    buffer = b""
                    overflow = 0

                # Decrypt message
                if self.aes_cipher is not None:
                    msg = self.aes_cipher.decrypt(og_msg)
                else:
                    msg = og_msg
            
                # Process messages
                if msg!=b"":
                    #Check if colapse is happening
                    overflow = CommunicationProtocol.check_collapse(msg)
                    if overflow>0:
                        buffer = og_msg[overflow:]
                        msg = msg[:overflow]
                        logging.debug("({}) (NodeConnection Run) Collapse detected: {}".format(self.get_name(), msg))

                    # Process message and count errors
                    exec_msgs,error = self.comm_protocol.process_message(msg)
                    if len(exec_msgs) > 0:
                        self.notify(Events.PROCESSED_MESSAGES_EVENT, (self,exec_msgs)) # Notify the parent node

                    # Error happened
                    if error:
                        self.terminate_flag.set()
                        logging.debug("({}) An error happened. Last error: {}".format(self.get_name(),msg))       

            except socket.timeout:
                logging.debug("({}) (NodeConnection Loop) Timeout".format(self.get_name()))
                self.terminate_flag.set()
                break

            except Exception as e:
                logging.debug("({}) (NodeConnection Loop) Exception: ".format(self.get_name()) + str(e))
                self.terminate_flag.set()
                break
        
        #Down Connection
        logging.debug("Closed connection: {}".format(self.get_name()))
        self.notify(Events.END_CONNECTION, self) # Notify the parent node
        self.socket.close()

    def stop(self,local=False):
        """
        Stop the connection.
        
        Args:
            local: If true, the connection will be closed without notifying the other node.
        """
        if not local:
            self.send(CommunicationProtocol.build_stop_msg())
        self.terminate_flag.set()

    ##################
    #    Messages    # 
    ##################

    # Send a message to the other node. Message sending isnt guaranteed
    def send(self, data): 
        """
        Send a message to the other node.

        Args:
            data: The message to send.

        Returns:
            True if the message was sent, False otherwise.

        """    
        # Check if the connection is still alive
        if not self.terminate_flag.is_set():
            try:
                # Encrypt message
                if self.aes_cipher is not None:
                    data = self.aes_cipher.add_padding(data) # -> It cant broke the model because it fills all the block space
                    data = self.aes_cipher.encrypt(data)
                # Send message
                self.socket_lock.acquire()
                self.socket.sendall(data)
                self.socket_lock.release()
                return True
            
            except Exception as e:
                # If some error happened, the connection is closed
                self.terminate_flag.set() #exit
                return False
        else:
            return False

    ###########################
    #    Command Callbacks    #
    ###########################

    def notify_heartbeat(self,node):
        """
        Notify that a heartbeat was received.
        """
        self.notify(Events.BEAT_RECEIVED_EVENT, node)

    def notify_conn_to(self, h, p):
        """
        Notify to the parent node that `CONN_TO` has been received.
        """
        self.notify(Events.CONN_TO, (h,p))

    def notify_start_learning(self, r, e):
        """
        Notify to the parent node that `START_LEARNING` has been received.
        """
        self.notify(Events.START_LEARNING, (r,e))

    def notify_stop_learning(self,cmd):
        """
        Notify to the parent node that `START_LEARNING` has been received.
        """
        self.notify(Events.STOP_LEARNING, None)

    def notify_params(self,params):
        """
        Notify to the parent node that `PARAMS` has been received.
        """
        self.notify(Events.PARAMS_RECEIVED, (params))

    def notify_metrics(self,round,loss,metric):
        """
        Notify to the parent node that `METRICS` has been received.
        """
        name = str(self.get_addr()[0]) + ":" + str(self.get_addr()[1])
        self.notify(Events.METRICS_RECEIVED, (name, round, loss, metric))

    def notify_train_set_votes(self,node,votes):
        """
        Set the last ready round of the other node.

        Args:
            round: The last ready round of the other node.
        """
        self.notify(Events.TRAIN_SET_VOTE_RECEIVED_EVENT, (node,votes))