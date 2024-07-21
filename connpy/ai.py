import openai
import time
import json
import re
import ast
from textwrap import dedent
from .core import nodes
from copy import deepcopy
from .hooks import ClassHook,MethodHook

@ClassHook
class ai:
    ''' This class generates a ai object. Containts all the information and methods to make requests to openAI chatGPT to run actions on the application.

    ### Attributes:  

        - model        (str): Model of GPT api to use. Default is gpt-4o-mini.

        - temp       (float): Value between 0 and 1 that control the randomness 
                              of generated text, with higher values increasing 
                              creativity. Default is 0.7.

        '''

    def __init__(self, config, org = None, api_key = None, model = None, temp = 0.7):
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

            - model   (str): Model of GPT api to use. Default is gpt-4o-mini. 

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
        if model:
            self.model = model
        else:
            try:
                self.model = self.config.config["openai"]["model"]
            except:
                self.model = "gpt-4o-mini"
        self.temp = temp
        self.__prompt = {}
        self.__prompt["original_system"] = """
            You are the AI chatbot and assistant of a network connection manager and automation app called connpy. When provided with user input analyze the input and extract the following information. If user wants to chat just reply and don't call a function:

            - type: Given a user input, identify the type of request they want to make. The input will represent one of two options: 

                1. "command" - The user wants to get information from devices by running commands.
                2. "list_nodes" - The user wants to get a list of nodes, devices, servers, or routers.
                The 'type' field should reflect whether the user input is a command or a request for a list of nodes.

            - filter: One or more regex patterns indicating the device or group of devices the command should be run on. The filter can have different formats, such as:
                - hostname
                - hostname@folder
                - hostname@subfolder@folder
                - partofhostname
                - @folder
                - @subfolder@folder
                - regex_pattern

                The filter should be extracted from the user input exactly as it was provided.
                Always preserve the exact filter pattern provided by the user, with no modifications. Do not process any regex, the application can do that.

    """ 
        self.__prompt["original_user"] = "Get the IP addresses of loopback0 for all routers from w2az1 and e1.*(prod|dev) and check if they have the ip 192.168.1.1"
        self.__prompt["original_assistant"] = {"name": "get_network_device_info", "arguments": "{\n  \"type\": \"command\",\n  \"filter\": [\"w2az1\",\"e1.*(prod|dev)\"]\n}"}
        self.__prompt["original_function"] = {}
        self.__prompt["original_function"]["name"] = "get_network_device_info"
        self.__prompt["original_function"]["descriptions"] = "You are the AI chatbot and assistant of a network connection manager and automation app called connpy. When provided with user input analyze the input and extract the information acording to the function, If user wants to chat just reply and don't call a function",
        self.__prompt["original_function"]["parameters"] = {}
        self.__prompt["original_function"]["parameters"]["type"] = "object"
        self.__prompt["original_function"]["parameters"]["properties"] = {}
        self.__prompt["original_function"]["parameters"]["properties"]["type"] = {}
        self.__prompt["original_function"]["parameters"]["properties"]["type"]["type"] = "string"
        self.__prompt["original_function"]["parameters"]["properties"]["type"]["description"] ="""
Categorize the user's request based on the operation they want to perform on the nodes. The requests can be classified into the following categories:

    1. "command" - This represents a request to retrieve specific information or configurations from nodes. An example would be: "go to routers in @office and get the config".

    2. "list_nodes" - This is when the user wants a list of nodes. An example could be: "get me the nodes in @office".
"""
        self.__prompt["original_function"]["parameters"]["properties"]["type"]["enum"] = ["command", "list_nodes"]
        self.__prompt["original_function"]["parameters"]["properties"]["filter"] = {}
        self.__prompt["original_function"]["parameters"]["properties"]["filter"]["type"] = "array"
        self.__prompt["original_function"]["parameters"]["properties"]["filter"]["items"] = {}
        self.__prompt["original_function"]["parameters"]["properties"]["filter"]["items"]["type"] = "string"
        self.__prompt["original_function"]["parameters"]["properties"]["filter"]["items"]["description"] = """One or more regex patterns indicating the device or group of devices the command should be run on.  The filter should be extracted from the user input exactly as it was provided. 
                The filter can have different formats, such as:
                - hostname
                - hostname@folder
                - hostname@subfolder@folder
                - partofhostname
                - @folder
                - @subfolder@folder
                - regex_pattern
                """
        self.__prompt["original_function"]["parameters"]["required"] = ["type", "filter"]
        self.__prompt["command_system"] = """
        For each OS listed below, provide the command(s) needed to perform the specified action, depending on the device OS (e.g., Cisco IOSXR router, Linux server).
        The application knows how to connect to devices via SSH, so you only need to provide the command(s) to run after connecting. This includes access configuration mode and commiting if required.
        If the commands needed are not for the specific OS type, just send an empty list (e.g., []). 
        Note: Preserving the integrity of user-provided commands is of utmost importance. If a user has provided a specific command to run, include that command exactly as it was given, even if it's not recognized or understood. Under no circumstances should you modify or alter user-provided commands.
    """
        self.__prompt["command_user"]= """
    input: show me the full configuration for all this devices:

    OS:
    cisco ios:
    """
        self.__prompt["command_assistant"] = {"name": "get_commands", "arguments": "{\n  \"cisco ios\": \"show running-configuration\"\n}"}
        self.__prompt["command_function"] = {}
        self.__prompt["command_function"]["name"] = "get_commands"
        self.__prompt["command_function"]["descriptions"] = """ 
        For each OS listed below, provide the command(s) needed to perform the specified action, depending on the device OS (e.g., Cisco IOSXR router, Linux server).
        The application knows how to connect to devices via SSH, so you only need to provide the command(s) to run after connecting. This includes access configuration mode and commiting if required.
        If the commands needed are not for the specific OS type, just send an empty list (e.g., []). 
    """
        self.__prompt["command_function"]["parameters"] = {}
        self.__prompt["command_function"]["parameters"]["type"] = "object"
        self.__prompt["command_function"]["parameters"]["properties"] = {}
        self.__prompt["confirmation_system"] = """
        Please analyze the user's input and categorize it as either an affirmation or negation. Based on this analysis, respond with:

            'true' if the input is an affirmation like 'do it', 'go ahead', 'sure', etc.
            'false' if the input is a negation.
            'none' If the input does not fit into either of these categories.
            """
        self.__prompt["confirmation_user"] = "Yes go ahead!"
        self.__prompt["confirmation_assistant"] = "True"
        self.__prompt["confirmation_function"] = {}
        self.__prompt["confirmation_function"]["name"] = "get_confirmation"
        self.__prompt["confirmation_function"]["descriptions"] = """ 
        Analize user request and respond:
    """
        self.__prompt["confirmation_function"]["parameters"] = {}
        self.__prompt["confirmation_function"]["parameters"]["type"] = "object"
        self.__prompt["confirmation_function"]["parameters"]["properties"] = {}
        self.__prompt["confirmation_function"]["parameters"]["properties"]["result"] = {}
        self.__prompt["confirmation_function"]["parameters"]["properties"]["result"]["description"] = """'true' if the input is an affirmation like 'do it', 'go ahead', 'sure', etc.
'false' if the input is a negation.
'none' If the input does not fit into either of these categories"""
        self.__prompt["confirmation_function"]["parameters"]["properties"]["result"]["type"] = "string"
        self.__prompt["confirmation_function"]["parameters"]["properties"]["result"]["enum"] = ["true", "false", "none"]
        self.__prompt["confirmation_function"]["parameters"]["properties"]["response"] = {}
        self.__prompt["confirmation_function"]["parameters"]["properties"]["response"]["description"] = "If the user don't message is not an affiramtion or negation, kindly ask the user to rephrase."
        self.__prompt["confirmation_function"]["parameters"]["properties"]["response"]["type"] = "string"
        self.__prompt["confirmation_function"]["parameters"]["required"] = ["result"]

    @MethodHook
    def _retry_function(self, function, max_retries, backoff_num, *args):
        #Retry openai requests
        retries = 0
        while retries < max_retries:
            try:
                myfunction = function(*args)
                break
            except:
                wait_time = backoff_num * (2 ** retries)
                time.sleep(wait_time)
                retries += 1
                continue
        if retries == max_retries:
            myfunction = False
        return myfunction

    @MethodHook
    def _clean_command_response(self, raw_response, node_list):
        # Parse response for command request to openAI GPT.
        info_dict = {}
        info_dict["commands"] = []
        info_dict["variables"] = {}
        info_dict["variables"]["__global__"] = {}
        for key, value in node_list.items():
            newvalue = {}
            commands = raw_response[value]
            # Ensure commands is a list
            if isinstance(commands, str):
                commands = [commands]
            # Determine the number of digits required for zero-padding
            num_commands = len(commands)
            num_digits = len(str(num_commands))

            for i, e in enumerate(commands, start=1):
                # Zero-pad the command number
                command_num = f"command{str(i).zfill(num_digits)}"
                newvalue[command_num] = e
                if f"{{command{i}}}" not in info_dict["commands"]:
                    info_dict["commands"].append(f"{{{command_num}}}")
                    info_dict["variables"]["__global__"][command_num] = ""
                info_dict["variables"][key] = newvalue
        return info_dict


    @MethodHook
    def _get_commands(self, user_input, nodes):
        #Send the request for commands for each device to openAI GPT.
        output_list = []
        command_function = deepcopy(self.__prompt["command_function"])
        node_list = {}
        for key, value in nodes.items():
            tags = value.get('tags', {})
            try:
                if os_value := tags.get('os'):
                    node_list[key] = os_value
                    output_list.append(f"{os_value}")
                    command_function["parameters"]["properties"][os_value] = {}
                    command_function["parameters"]["properties"][os_value]["type"] = "array"
                    command_function["parameters"]["properties"][os_value]["description"] = f"OS: {os_value}"
                    command_function["parameters"]["properties"][os_value]["items"] = {}
                    command_function["parameters"]["properties"][os_value]["items"]["type"] = "string" 
            except:
                pass
        output_str = "\n".join(list(set(output_list)))
        command_input = f"input: {user_input}\n\nOS:\n{output_str}"
        message = []
        message.append({"role": "system", "content": dedent(self.__prompt["command_system"]).strip()})
        message.append({"role": "user", "content": dedent(self.__prompt["command_user"]).strip()})
        message.append({"role": "assistant", "content": None, "function_call": self.__prompt["command_assistant"]})
        message.append({"role": "user", "content": command_input})
        functions = [command_function]
        response = openai.ChatCompletion.create(
            model=self.model,
            messages=message,
            functions=functions,
            function_call={"name": "get_commands"},
            temperature=self.temp
            )
        output = {}
        result = response["choices"][0]["message"].to_dict()
        json_result = json.loads(result["function_call"]["arguments"])
        output["response"] = self._clean_command_response(json_result, node_list)
        return output

    @MethodHook
    def _get_filter(self, user_input, chat_history = None):
        #Send the request to identify the filter and other attributes from the user input to GPT.
        message = []
        message.append({"role": "system", "content": dedent(self.__prompt["original_system"]).strip()})
        message.append({"role": "user", "content": dedent(self.__prompt["original_user"]).strip()})
        message.append({"role": "assistant", "content": None, "function_call": self.__prompt["original_assistant"]})
        functions = [self.__prompt["original_function"]]
        if not chat_history:
            chat_history = []
        chat_history.append({"role": "user", "content": user_input})
        message.extend(chat_history)
        response = openai.ChatCompletion.create(
            model=self.model,
            messages=message,
            functions=functions,
            function_call="auto",
            temperature=self.temp,
            top_p=1
            )

        def extract_quoted_strings(text):
            pattern = r'["\'](.*?)["\']'
            matches = re.findall(pattern, text)
            return matches
        expected = extract_quoted_strings(user_input)
        output = {}
        result = response["choices"][0]["message"].to_dict()
        if result["content"]:
            output["app_related"] = False
            chat_history.append({"role": "assistant", "content": result["content"]})
            output["response"] = result["content"]
        else:
            json_result = json.loads(result["function_call"]["arguments"])
            output["app_related"] = True
            output["filter"] = json_result["filter"]
            output["type"] = json_result["type"]
            chat_history.append({"role": "assistant", "content": result["content"], "function_call": {"name": result["function_call"]["name"], "arguments": json.dumps(json_result)}})
        output["expected"] = expected
        output["chat_history"] = chat_history
        return output
        
    @MethodHook
    def _get_confirmation(self, user_input):
        #Send the request to identify if user is confirming or denying the task
        message = []
        message.append({"role": "user", "content": user_input})
        functions = [self.__prompt["confirmation_function"]]
        response = openai.ChatCompletion.create(
            model=self.model,
            messages=message,
            functions=functions,
            function_call={"name": "get_confirmation"},
            temperature=self.temp,
            top_p=1
            )
        result = response["choices"][0]["message"].to_dict()
        json_result = json.loads(result["function_call"]["arguments"])
        output = {}
        if json_result["result"] == "true":
            output["result"] = True
        elif json_result["result"] == "false":
            output["result"] = False
        elif json_result["result"] == "none":
            output["result"] = json_result["response"]
        return output

    @MethodHook
    def confirm(self, user_input, max_retries=3, backoff_num=1):
        '''
        Send the user input to openAI GPT and verify if response is afirmative or negative.

        ### Parameters:  

            - user_input (str): User response confirming or denying.

        ### Optional Parameters:  

            - max_retries (int): Maximum number of retries for gpt api.
            - backoff_num (int): Backoff factor for exponential wait time
                                 between retries.

        ### Returns:  

            bool or str: True, False or str if AI coudn't understand the response
        '''
        result = self._retry_function(self._get_confirmation, max_retries, backoff_num, user_input)
        if result:
            output = result["result"]
        else:
            output = f"{self.model} api is not responding right now, please try again later."
        return output

    @MethodHook
    def ask(self, user_input, dryrun = False, chat_history = None,  max_retries=3, backoff_num=1):
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

            - dryrun       (bool): Set to true to get the arguments to use to
                                   run in the app. Default is false and it
                                   will run the actions directly.
            - chat_history (list): List in gpt api format for the chat history.
            - max_retries   (int): Maximum number of retries for gpt api.
            - backoff_num   (int): Backoff factor for exponential wait time
                                   between retries.

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
                  - chat_history: The chat history between user and chatbot.
                    It can be used as an attribute for next request.
                
                    

        '''
        output = {}
        output["dryrun"] = dryrun
        output["input"] = user_input
        original = self._retry_function(self._get_filter, max_retries, backoff_num, user_input, chat_history)
        if not original:
            output["app_related"] = False
            output["response"] = f"{self.model} api is not responding right now, please try again later."
            return output
        output["app_related"] = original["app_related"]
        output["chat_history"] = original["chat_history"]
        if not output["app_related"]:
            output["response"] = original["response"]
        else:
            type = original["type"]
            if "filter" in original:
                output["filter"] = original["filter"]
                if not self.config.config["case"]:
                    if isinstance(output["filter"], list):
                        output["filter"] = [item.lower() for item in output["filter"]]
                    else:
                        output["filter"] = output["filter"].lower()
                if not dryrun or type == "command":
                    thisnodes = self.config._getallnodesfull(output["filter"])
                    output["nodes"] = list(thisnodes.keys())
            if not type == "command":
                output["action"] = "list_nodes"
            else:
                if thisnodes:
                    commands = self._retry_function(self._get_commands, max_retries, backoff_num, user_input, thisnodes)
                else:
                    output["app_related"] = False
                    filterlist = ", ".join(output["filter"])
                    output["response"] = f"I'm sorry, I coudn't find any device with filter{'s' if len(output['filter']) != 1 else ''}: {filterlist}."
                    return output
                if not commands:
                    output["app_related"] = False
                    output["response"] = f"{self.model} api is not responding right now, please try again later."
                    return output
                output["args"] = {}
                output["args"]["commands"] = commands["response"]["commands"]
                output["args"]["vars"] = commands["response"]["variables"]
                output["nodes"] = [item for item in output["nodes"] if output["args"]["vars"].get(item)]
                if original.get("expected"):
                    output["args"]["expected"] = original["expected"]
                    output["action"] = "test"
                else:
                    output["action"] = "run"
                if dryrun:
                    output["task"] = []
                    if output["action"] == "test":
                        output["task"].append({"Task": "Verify if expected value is in command(s) output"})
                        output["task"].append({"Expected value to verify": output["args"]["expected"]})
                    elif output["action"] == "run":
                        output["task"].append({"Task": "Run command(s) on devices and return output"})
                    varstocommands = deepcopy(output["args"]["vars"])
                    del varstocommands["__global__"]
                    output["task"].append({"Devices": varstocommands})
                if not dryrun:
                    mynodes = nodes(self.config.getitems(output["nodes"]),config=self.config)
                    if output["action"] == "test":
                        output["result"] = mynodes.test(**output["args"])
                        output["logs"] = mynodes.output
                    elif output["action"] == "run":
                        output["result"] = mynodes.run(**output["args"])
        return output







