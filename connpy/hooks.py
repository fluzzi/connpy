#!/usr/bin/env python3
#Imports
from functools import wraps, partial, update_wrapper

#functions and classes

class MethodHook:
    """Decorator class to enable Methods hooking"""

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
                print(f"{self.func.__name__} Pre-hook {hook.__name__} raised an exception: {e}")

        try:
            result = self.func(*args, **kwargs)

        finally:
            # Execute post-hooks after the original function
            for hook in self.post_hooks:
                try:
                    result = hook(*args, **kwargs, result=result)  # Pass result to hooks
                except Exception as e:
                    print(f"{self.func.__name__} Post-hook {hook.__name__} raised an exception: {e}")

        return result

    def __get__(self, instance, owner):
        if not instance:
            return self
        else:
            return self.make_callable(instance)

    def make_callable(self, instance):
        # Returns a callable that also allows access to hook registration methods
        callable_instance = partial(self.__call__, instance)
        callable_instance.register_pre_hook = self.register_pre_hook
        callable_instance.register_post_hook = self.register_post_hook
        return callable_instance

    def register_pre_hook(self, hook):
        """Register a function to be called before the original function"""
        self.pre_hooks.append(hook)

    def register_post_hook(self, hook):
        """Register a function to be called after the original function"""
        self.post_hooks.append(hook)

class ClassHook:
    """Decorator class to enable Class Modifying"""
    def __init__(self, cls):
        self.cls = cls
        update_wrapper(self, cls, updated=())  # Update wrapper without changing underlying items
        # Initialize deferred class hooks if they don't already exist
        if not hasattr(cls, 'deferred_class_hooks'):
            cls.deferred_class_hooks = []

    def __call__(self, *args, **kwargs):
        instance = self.cls(*args, **kwargs)
        # Attach instance-specific modify method
        instance.modify = self.make_instance_modify(instance)
        # Apply any deferred modifications
        for hook in self.cls.deferred_class_hooks:
            hook(instance)
        return instance

    def __getattr__(self, name):
        """Delegate attribute access to the class."""
        return getattr(self.cls, name)

    def modify(self, modification_func):
        """Queue a modification to be applied to all future instances of the class."""
        self.cls.deferred_class_hooks.append(modification_func)

    def make_instance_modify(self, instance):
        """Create a modify function that is bound to a specific instance."""
        def modify_instance(modification_func):
            """Modify this specific instance."""
            modification_func(instance)
        return modify_instance

