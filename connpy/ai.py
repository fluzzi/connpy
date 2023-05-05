import openai
import json
import re
import ast
from textwrap import dedent
from .core import nodes

class ai:
    ''' This class generates a ai object. Containts all the information and methods to make requests to openAI chatGPT to run actions on the application.

    ### Attributes:  

        - model        (str): Model of GPT api to use. Default is gpt-3.5-turbo.

        - temp       (float): Value between 0 and 1 that control the randomness 
                              of generated text, with higher values increasing 
                              creativity. Default is 0.7.

        '''

    def __init__(self, config, org = None, api_key = None, model = "gpt-3.5-turbo", temp = 0.7):
        ''' 
            
        ### Parameters:  

            - config (obj): Pass the object created with class configfile with 
                            key for decryption and extra configuration if you 
                            are using connection manager.  

        ### Optional Parameters:  

            - org     (str): A unique token identifying the user organization
                             to interact with the API.

            - api_key (str): A unique authentication token required to access 
                             and interact with the API.

            - model   (str): Model of GPT api to use. Default is gpt-3.5-turbo. 

            - temp  (float): Value between 0 and 1 that control the randomness 
                             of generated text, with higher values increasing 
                             creativity. Default is 0.7.
   

        '''
        self.config = config
        if org:
            openai.organization = org
        else:
            try: 
                openai.organization = self.config.config["openai"]["organization"]
            except:
                raise ValueError("Missing openai organization")
        if api_key:
            openai.api_key = api_key
        else:
            try: 
                openai.api_key = self.config.config["openai"]["api_key"]
            except:
                raise ValueError("Missing openai api_key")
        self.__prompt = {}
        self.__prompt["original_system"] = """
            When provided with user input for an SSH connection management application, analyze the input and extract the following information:

            - app_related: True if the input is related to the application's purpose and the request is understood; False if the input is not related, not understood, or if mandatory information like filter is missing.
            - type: Given a user input, identify the type of request they want to make. The input will represent one of two options: 
            1. "command" - The user wants to get information from devices by running commands.
            2. "list_nodes" - The user wants to get a list of nodes, devices, servers, or routers.
            Response wich command or list_nodes type depending on the request.
            - filter: One or more regex patterns indicating the device or group of devices the command should be run on, returned as a Python list (e.g., ['hostname', 'hostname@folder', '@subfolder@folder']). The filter can have different formats, such as:
                - hostname
                - hostname@folder
                - hostname@subfolder@folder
                - partofhostname
                - @folder
                - @subfolder@folder
                - regex_pattern
                The filter should be extracted from the user input exactly as it was provided.
                Always preserve the exact filter pattern provided by the user, with no modifications. Do not process any regex, the application can do that.
                If no filter is specified, set it to None.
            - Expected: A value representing an expected output to search for when running the command. For the user this is a optional value. Set it to 'None' if no value was captured.
              expected value should ALWAYS come from the user input explicitly.
            - response: An optional field to be filled when app_related is False or when providing an explanation related to the app. This is where you can engage in small talk, answer questions not related to the app, or provide explanations about the extracted information.
                
            Always respond in the following format:
                
                app_related: {{app_related}}
                Type: {{command}}
                Filter: {{filter}}
                Expected: {{expected}}
                Response: {{response}}
    """ 
        self.__prompt["original_user"] = "Get the IP addresses of loopback0 for all routers from w2az1 and e1.*(prod|dev) and check if they have the ip 192.168.1.1"
        self.__prompt["original_assistant"] = "app_related: True\nType: Command\nFilter: ['w2az1', 'e1.*(prod|dev)']\nExpected: 192.168.1.1"
        self.__prompt["command_system"] = """
    For each device listed below, provide the command(s) needed to perform the specified action, depending on the device OS (e.g., Cisco IOSXR router, Linux server). Always format your response as a Python list (e.g., ['command1', 'command2']). 

    Note that the application knows how to connect to devices via SSH, so you only need to provide the command(s) to run after connecting. 

    If the commands needed are not for the specific OS type, just send an empty list (e.g., []).

    It is crucial to always include the device name provided in your response, even when there is only one device.

    your response has to be always like this:
        node1: ["command1", "command2"]
        node2: ["command1", "command2", "command3"]
        node1@folder: ["command1"]
        Node4@subfolder@folder: []
    """
        self.__prompt["command_user"]= """
    input: show me the full configuration for all this devices:

    Devices:
    router1: cisco ios
    """
        self.__prompt["command_assistant"]= """
    router1: ['show running-config']
    """
        self.model = model
        self.temp = temp

    def _clean_original_response(self, raw_response):
        #Parse response for first request to openAI GPT.
        info_dict = {}
        info_dict["app_related"] = False
        current_key = "response"
        for line in raw_response.split("\n"):
            if line.strip() == "":
                line = "\n"
            possible_keys = ["app_related", "type", "filter", "expected", "response"]
            if ':' in line and (key := line.split(':', 1)[0].strip().lower()) in possible_keys:
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip()
                # Convert "true" or "false" (case-insensitive) to Python boolean
                if value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False
                elif value.lower() == "none":
                    value = None
                if key == "filter":
                    value = ast.literal_eval(value)
                #store in dictionary
                info_dict[key] = value
                current_key = key
            else:
                if current_key == "response":
                    if "response" in info_dict:
                        info_dict[current_key] += "\n" + line
                    else:
                        info_dict[current_key] = line

        return info_dict

    def _clean_command_response(self, raw_response):
        #Parse response for command request to openAI GPT.
        info_dict = {}
        info_dict["commands"] = []
        info_dict["variables"] = {}
        info_dict["variables"]["__global__"] = {}
        for line in raw_response.split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip()
                newvalue = {}
                try:
                    value = ast.literal_eval(value.strip())
                    for i,e in enumerate(value, start=1):
                        newvalue[f"command{i}"] = e
                        if f"{{command{i}}}" not in info_dict["commands"]:
                            info_dict["commands"].append(f"{{command{i}}}")
                            info_dict["variables"]["__global__"][f"command{i}"] = ""
                    info_dict["variables"][key] = newvalue
                except:
                    pass
        return info_dict


    def _get_commands(self, user_input, nodes):
        #Send the request for commands for each device to openAI GPT.
        output_list = []
        for key, value in nodes.items():
            tags = value.get('tags', {})
            try:
                os_value = tags.get('os', '')
            except:
                os_value = ""
            output_list.append(f"{key}: {os_value}")
        output_str = "\n".join(output_list)
        command_input = f"input: {user_input}\n\nDevices:\n{output_str}"
        message = []
        message.append({"role": "system", "content": dedent(self.__prompt["command_system"])})
        message.append({"role": "user", "content": dedent(self.__prompt["command_user"])})
        message.append({"role": "assistant", "content": dedent(self.__prompt["command_assistant"])})
        message.append({"role": "user", "content": command_input})
        response = openai.ChatCompletion.create(
            model=self.model,
            messages=message,
            temperature=self.temp
            )
        output = {}
        output["dict_response"] = response
        output["raw_response"] = response["choices"][0]["message"]["content"] 
        output["response"] = self._clean_command_response(output["raw_response"])
        return output

    def _get_filter(self, user_input):
        #Send the request to identify the filter and other attributes from the user input to GPT.
        message = []
        message.append({"role": "system", "content": dedent(self.__prompt["original_system"])})
        message.append({"role": "user", "content": dedent(self.__prompt["original_user"])})
        message.append({"role": "assistant", "content": dedent(self.__prompt["original_assistant"])})
        message.append({"role": "user", "content": user_input})

        response = openai.ChatCompletion.create(
            model=self.model,
            messages=message,
            temperature=self.temp,
            top_p=1
            )

        output = {}
        output["dict_response"] = response
        output["raw_response"] = response["choices"][0]["message"]["content"] 
        clear_response = self._clean_original_response(output["raw_response"])
        output["response"] = self._clean_original_response(output["raw_response"])
        return output
        
    def ask(self, user_input, dryrun = False):
        '''
        Send the user input to openAI GPT and parse the response to run an action in the application.

        ### Parameters:  

            - user_input (str): Request to send to openAI that will be parsed
                                and returned to execute on the application.
                                AI understands the following tasks:
                                - Run a command on a group of devices.
                                - List a group of devices.
                                - Test a command on a group of devices
                                  and verify if the output contain an
                                  expected value.

        ### Optional Parameters:  

            - dryrun  (bool): Set to true to get the arguments to use to run
                              in the app. Default is false and it will run 
                              the actions directly.

        ### Returns:  

            dict: Dictionary formed with the following keys:
                  - input: User input received
                  - app_related: True if GPT detected the request to be related
                    to the application.
                  - dryrun: True/False
                  - response: If the request is not related to the app. this
                    key will contain chatGPT answer.
                  - action: The action detected by the AI to run in the app.
                  - filter: If it was detected by the AI, the filter used
                    to get the list of nodes to work on.
                  - nodes: If it's not a dryrun, the list of nodes matched by
                    the filter.
                  - args: A dictionary of arguments required to run command(s)
                    on the nodes.
                  - result: A dictionary with the output of the commands or 
                    the test.
                    

        '''
        output = {}
        original = self._get_filter(user_input)
        output["input"] = user_input
        output["app_related"] = original["response"]["app_related"]
        output["dryrun"] = dryrun
        if not output["app_related"]:
            output["response"] = original["response"]["response"]
        else:
            type = original["response"]["type"].lower()
            if "filter" in original["response"]:
                output["filter"] = original["response"]["filter"]
                if not self.config.config["case"]:
                    if isinstance(output["filter"], list):
                        output["filter"] = [item.lower() for item in output["filter"]]
                    else:
                        output["filter"] = output["filter"].lower()
                if not dryrun or type == "command":
                    thisnodes = self.config._getallnodesfull(output["filter"])
                    output["nodes"] = list(thisnodes.keys())
            if not type == "command":
                output["action"] = type
            else:
                commands = self._get_commands(user_input, thisnodes)
                output["args"] = {}
                output["args"]["commands"] = commands["response"]["commands"]
                output["args"]["vars"] = commands["response"]["variables"]
                if original["response"]["expected"]:
                    output["args"]["expected"] = original["response"]["expected"]
                    output["action"] = "test"
                else:
                    output["action"] = "run"
                if not dryrun:
                    mynodes = nodes(self.config.getitems(output["nodes"]),config=self.config)
                    if output["action"] == "test":
                        output["result"] = mynodes.test(**output["args"])
                    elif output["action"] == "run":
                        output["result"] = mynodes.run(**output["args"])
        return output







