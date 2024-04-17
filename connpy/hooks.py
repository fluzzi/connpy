#!/usr/bin/env python3
#Imports
from functools import wraps

#functions and classes

class ConfigHook:
    """Decorator class to enable Config save hooking"""

    def __init__(self, func):
        self.func = func
        self.pre_hooks = []   # List to store registered pre-hooks
        self.post_hooks = []  # List to store registered post-hooks
        wraps(func)(self)

    def __call__(self, *args, **kwargs):
        # Execute pre-hooks before the original function
        for hook in self.pre_hooks:
            try:
                args, kwargs = hook(*args, **kwargs)
            except Exception as e:
                print(f"ConfigHook Pre-hook raised an exception: {e}")

        try:
            # Execute original function
            result = self.func(self.instance, *args, **kwargs)

        finally:
            # Execute post-hooks after the original function
            for hook in self.post_hooks:
                try:
                    result = hook(*args, **kwargs, result=result)  # Pass result to hooks
                except Exception as e:
                    print(f"ConfigHook Post-hook raised an exception: {e}")

        return result

    def __get__(self, instance, owner):
        self.instance = instance
        return self

    def register_pre_hook(self, hook):
        """Register a function to be called before the original function"""
        self.pre_hooks.append(hook)

    def register_post_hook(self, hook):
        """Register a function to be called after the original function"""
        self.post_hooks.append(hook)
