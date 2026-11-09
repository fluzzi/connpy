#!/usr/bin/env python3
#Imports
from functools import update_wrapper, wraps

from . import printer

#functions and classes

class MethodHook:
    """Decorator class to enable Methods hooking"""

    def __init__(self, func):
        self.func = func
        self.pre_hooks = []   # List to store registered class-level pre-hooks
        self.post_hooks = []  # List to store registered class-level post-hooks
        wraps(func)(self)

    def register_pre_hook(self, hook):
        """Register a function to be called before the original function"""
        if hook not in self.pre_hooks:
            self.pre_hooks.append(hook)

    def register_post_hook(self, hook):
        """Register a function to be called after the original function"""
        if hook not in self.post_hooks:
            self.post_hooks.append(hook)

    def __call__(self, *args, **kwargs):
        # Fallback when called directly on descriptor or standalone function
        inst_pre = []
        inst_post = []
        if args and hasattr(args[0], "__dict__"):
            inst_dict = args[0].__dict__
            inst_pre = inst_dict.get("_method_pre_hooks", {}).get(self.func.__name__, []) if isinstance(inst_dict.get("_method_pre_hooks"), dict) else []
            inst_post = inst_dict.get("_method_post_hooks", {}).get(self.func.__name__, []) if isinstance(inst_dict.get("_method_post_hooks"), dict) else []

        all_pre = list(self.pre_hooks) + [h for h in inst_pre if h not in self.pre_hooks]
        for hook in all_pre:
            try:
                args, kwargs = hook(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                hook_name = getattr(hook, "__name__", str(hook))
                printer.error(f"{self.func.__name__} Pre-hook {hook_name} raised an exception: {e}")

        result = self.func(*args, **kwargs)

        all_post = list(self.post_hooks) + [h for h in inst_post if h not in self.post_hooks]
        for hook in all_post:
            try:
                result = hook(*args, **kwargs, result=result)
            except Exception as e:  # noqa: BLE001
                hook_name = getattr(hook, "__name__", str(hook))
                printer.error(f"{self.func.__name__} Post-hook {hook_name} raised an exception: {e}")

        return result

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return self.make_callable(instance)

    def make_callable(self, instance):
        # Returns a callable bound to instance with per-instance hook registration
        func_name = self.func.__name__

        def bound_method(*args, **kwargs):
            # Pre-hooks: class-level followed by instance-level
            if hasattr(instance, "__dict__"):
                inst_pre = instance.__dict__.get("_method_pre_hooks", {}).get(func_name, [])
                inst_post = instance.__dict__.get("_method_post_hooks", {}).get(func_name, [])
            else:
                inst_pre = instance._method_pre_hooks.get(func_name, []) if hasattr(instance, "_method_pre_hooks") else []
                inst_post = instance._method_post_hooks.get(func_name, []) if hasattr(instance, "_method_post_hooks") else []

            all_pre = list(self.pre_hooks) + [h for h in inst_pre if h not in self.pre_hooks]
            call_args = (instance,) + args

            for hook in all_pre:
                try:
                    call_args, kwargs = hook(*call_args, **kwargs)
                except Exception as e:  # noqa: BLE001
                    hook_name = getattr(hook, "__name__", str(hook))
                    printer.error(f"{func_name} Pre-hook {hook_name} raised an exception: {e}")

            result = self.func(*call_args, **kwargs)

            all_post = list(self.post_hooks) + [h for h in inst_post if h not in self.post_hooks]
            for hook in all_post:
                try:
                    result = hook(*call_args, **kwargs, result=result)
                except Exception as e:  # noqa: BLE001
                    hook_name = getattr(hook, "__name__", str(hook))
                    printer.error(f"{func_name} Post-hook {hook_name} raised an exception: {e}")

            return result

        wraps(self.func)(bound_method)

        def register_pre_hook(hook):
            if hasattr(instance, "__dict__"):
                hooks = instance.__dict__.setdefault("_method_pre_hooks", {}).setdefault(func_name, [])
            else:
                if not hasattr(instance, "_method_pre_hooks"):
                    instance._method_pre_hooks = {}
                hooks = instance._method_pre_hooks.setdefault(func_name, [])
            if hook not in hooks:
                hooks.append(hook)

        def register_post_hook(hook):
            if hasattr(instance, "__dict__"):
                hooks = instance.__dict__.setdefault("_method_post_hooks", {}).setdefault(func_name, [])
            else:
                if not hasattr(instance, "_method_post_hooks"):
                    instance._method_post_hooks = {}
                hooks = instance._method_post_hooks.setdefault(func_name, [])
            if hook not in hooks:
                hooks.append(hook)

        bound_method.register_pre_hook = register_pre_hook
        bound_method.register_post_hook = register_post_hook
        bound_method.__self__ = instance
        bound_method.__func__ = self.func

        return bound_method


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
