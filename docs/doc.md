Module conn
===========

Classes
-------

`configfile(conf=None, *, key=None)`
:   

    ### Methods

    `createconfig(self, conf)`
    :

    `createkey(self, keyfile)`
    :

    `getitem(self, unique, keys=None)`
    :

    `loadconfig(self, conf)`
    :

    `saveconfig(self, conf)`
    :

`node(unique, host, options='', logs='', password='', port='', protocol='', user='', config='')`
:   This class generates a node object. Containts all the information and methods to connect and interact with a device using ssh or telnet.
    
    Attributes:  
    
        - output (str) -- Output of the commands you ran with run or test 
                       -- method.  
        - result(bool) -- True if expected value is found after running 
                       -- the commands using test method.
        
    
        
    Parameters:  
    
        - unique   (str) -- Unique name to assign to the node.  
        - host     (str) -- IP address or hostname of the node.  
    
    Optional Parameters:  
    
        - options  (str) -- Additional options to pass the ssh/telnet for
                         -- connection.  
        - logs     (str) -- Path/file for storing the logs. You can use 
                         -- ${unique},${host}, ${port}, ${user}, ${protocol} 
                         -- as variables.  
        - password (str) -- Encrypted or plaintext password.  
        - port     (str) -- Port to connect to node, default 22 for ssh and 23 
                         -- for telnet.  
        - protocol (str) -- Select ssh or telnet. Default is ssh.  
        - user     (str) -- Username to of the node.  
        - config   (obj) -- Pass the object created with class configfile with 
                         -- key for decryption and extra configuration if you 
                         -- are using connection manager.

    ### Methods

    `interact(self, debug=False)`
    :   Allow user to interact with the node directly, mostly used by connection manager.
        
        Optional Parameters:  
        
            - debug (bool) -- If True, display all the connecting information 
                           -- before interact. Default False.

    `run(self, commands, *, folder='', prompt='>$|#$|\\$$|>.$|#.$|\\$.$', stdout=False)`
    :   Run a command or list of commands on the node and return the output.
        
        Parameters:  
        
            - commands (str/list) -- Commands to run on the node. Should be 
                                  -- str or a list of str.
        
        Optional Named Parameters:  
        
            - folder (str)  -- Path where output log should be stored, leave 
                            -- empty to disable logging.  
            - prompt (str)  -- Prompt to be expected after a command is finished 
                            -- running. Usually linux uses  ">" or EOF while 
                            -- routers use ">" or "#". The default value should 
                            -- work for most nodes. Change it if your connection 
                            -- need some special symbol.  
            - stdout (bool) -- Set True to send the command output to stdout. 
                            -- default False.
        
        Returns:  
        
            str -> Output of the commands you ran on the node.

    `test(self, commands, expected, *, prompt='>$|#$|\\$$|>.$|#.$|\\$.$')`
    :   Run a command or list of commands on the node, then check if expected value appears on the output after the last command.
        
        Parameters:  
        
            - commands (str/list) -- Commands to run on the node. Should be
                                  -- str or list of str.  
            - expected (str)      -- Expected text to appear after running 
                                  -- all the commands on the node.
        
        Optional Named Parameters: 
        
            - prompt (str) -- Prompt to be expected after a command is finished
                           -- running. Usually linux uses  ">" or EOF while 
                           -- routers use ">" or "#". The default value should 
                           -- work for most nodes. Change it if your connection 
                           -- need some special symbol.
        
        Returns: 
        
            bool -> true if expected value is found after running the commands 
                    false if prompt is found before.

`nodes(nodes: dict, config='')`
:   This class generates a nodes object. Contains a list of node class objects and methods to run multiple tasks on nodes simultaneously.
    
    ### Attributes:  
    
        - nodelist (list): List of node class objects passed to the init 
                           function.  
    
        - output   (dict): Dictionary formed by nodes unique as keys, output of the commands you ran on the node as 
                           value. Created after running methods run or test.  
    
        - result   (dict): Dictionary formed by nodes unique as keys, value 
                           is True if expected value is found after running 
                           the commands, False if prompt is found before. 
                           Created after running method test.  
    
        - <unique> (obj):  For each item in nodelist, there is an attribute
                           generated with the node unique.
        
    
    ### Parameters:  
    
        - nodes (dict): Dictionary formed by node information:  
                        Keys: Unique name for each node.  
                        Mandatory Subkeys: host(str).  
                        Optional Subkeys: options(str), logs(str), password(str),
                        port(str), protocol(str), user(str).  
                        For reference on subkeys check node class.
    
    Optional Parameters:  
    
        - config (obj): Pass the object created with class configfile with key 
                        for decryption and extra configuration if you are using 
                        connection manager.

    ### Methods

    `run(self, commands, *, folder=None, prompt=None, stdout=None, parallel=10)`
    :   Run a command or list of commands on all the nodes in nodelist.
        
        Parameters:  
            commands (str/list): Commands to run on the node. Should be a str or a list of str.
        
        Optional Named Parameters:  
            folder   (str): Path where output log should be stored, leave empty to disable logging.  
            prompt   (str): Prompt to be expected after a command is finished running. Usually linux uses  ">" or EOF while routers use ">" or "#". The default value should work for most nodes. Change it if your connection need some special symbol.  
            stdout   (bool): Set True to send the command output to stdout. default False.  
            parallel (int): Number of nodes to run the commands simultaneously. Default is 10, if there are more nodes that this value, nodes are groups in groups with max this number of members.
        
        Returns:  
            dict: Dictionary formed by nodes unique as keys, Output of the commands you ran on the node as value.

    `test(self, commands, expected, *, prompt=None, parallel=10)`
    :   Run a command or list of commands on all the nodes in nodelist, then check if expected value appears on the output after the last command.
        
        Parameters:  
            commands (str/list): Commands to run on the node. Should be a str or a list of str.  
            commands (str): Expected text to appear after running all the commands on the node.
        
        Optional Named Parameters:  
            prompt   (str): Prompt to be expected after a command is finished running. Usually linux uses  ">" or EOF while routers use ">" or "#". The default value should work for most nodes. Change it if your connection need some special symbol.
        
        Returns:  
            dict: Dictionary formed by nodes unique as keys, value is True if expected value is found after running the commands, False if prompt is found before.
