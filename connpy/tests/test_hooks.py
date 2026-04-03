"""Tests for connpy.hooks module — MethodHook and ClassHook."""
import pytest
from connpy.hooks import MethodHook, ClassHook


# =========================================================================
# MethodHook Tests
# =========================================================================

class TestMethodHook:
    def test_basic_call(self):
        """Decorated function executes normally."""
        @MethodHook
        def add(a, b):
            return a + b
        assert add(2, 3) == 5

    def test_pre_hook_modifies_args(self):
        """Pre-hook can modify arguments before execution."""
        @MethodHook
        def greet(name):
            return f"Hello {name}"

        def uppercase_hook(name):
            return (name.upper(),), {}

        greet.register_pre_hook(uppercase_hook)
        assert greet("world") == "Hello WORLD"

    def test_post_hook_modifies_result(self):
        """Post-hook can modify the return value."""
        @MethodHook
        def compute(x):
            return x * 2

        def double_result(*args, **kwargs):
            return kwargs["result"] * 2

        compute.register_post_hook(double_result)
        assert compute(5) == 20  # 5*2=10, then 10*2=20

    def test_multiple_pre_hooks_order(self):
        """Pre-hooks execute in registration order."""
        calls = []

        @MethodHook
        def func(x):
            return x

        def hook1(x):
            calls.append("hook1")
            return (x,), {}

        def hook2(x):
            calls.append("hook2")
            return (x,), {}

        func.register_pre_hook(hook1)
        func.register_pre_hook(hook2)
        func(1)
        assert calls == ["hook1", "hook2"]

    def test_multiple_post_hooks_order(self):
        """Post-hooks execute in registration order."""
        calls = []

        @MethodHook
        def func(x):
            return x

        def hook1(*args, **kwargs):
            calls.append("hook1")
            return kwargs["result"]

        def hook2(*args, **kwargs):
            calls.append("hook2")
            return kwargs["result"]

        func.register_post_hook(hook1)
        func.register_post_hook(hook2)
        func(1)
        assert calls == ["hook1", "hook2"]

    def test_pre_hook_exception_continues(self, capsys):
        """If a pre-hook raises, the function still executes."""
        @MethodHook
        def func(x):
            return x + 1

        def bad_hook(x):
            raise RuntimeError("broken hook")

        func.register_pre_hook(bad_hook)
        # Should not raise — the hook error is printed but execution continues
        result = func(5)
        assert result == 6

    def test_post_hook_exception_continues(self, capsys):
        """If a post-hook raises, the result is still returned."""
        @MethodHook
        def func(x):
            return x + 1

        def bad_hook(*args, **kwargs):
            raise RuntimeError("broken post hook")

        func.register_post_hook(bad_hook)
        result = func(5)
        assert result == 6

    def test_method_hook_as_instance_method(self):
        """MethodHook works as a descriptor on a class."""
        class MyClass:
            @MethodHook
            def double(self, x):
                return x * 2

        obj = MyClass()
        assert obj.double(5) == 10

    def test_method_hook_instance_hook_registration(self):
        """Can register hooks via instance method access."""
        class MyClass:
            @MethodHook
            def process(self, x):
                return x

        def add_ten(*args, **kwargs):
            return kwargs["result"] + 10

        obj = MyClass()
        obj.process.register_post_hook(add_ten)
        assert obj.process(5) == 15


# =========================================================================
# ClassHook Tests
# =========================================================================

class TestClassHook:
    def test_creates_instance(self):
        """ClassHook still creates instances normally."""
        @ClassHook
        class MyClass:
            def __init__(self, value):
                self.value = value

        obj = MyClass(42)
        assert obj.value == 42

    def test_modify_future_instances(self):
        """modify() affects all future instances."""
        @ClassHook
        class MyClass:
            def __init__(self):
                self.x = 1

        def set_x_to_99(instance):
            instance.x = 99

        MyClass.modify(set_x_to_99)
        obj = MyClass()
        assert obj.x == 99

    def test_modify_does_not_affect_past(self):
        """modify() does not affect already-created instances."""
        @ClassHook
        class MyClass:
            def __init__(self):
                self.x = 1

        old_obj = MyClass()

        def set_x_to_99(instance):
            instance.x = 99

        MyClass.modify(set_x_to_99)
        assert old_obj.x == 1  # Not affected
        assert MyClass().x == 99  # New instance IS affected

    def test_instance_modify(self):
        """instance.modify() only affects that specific instance."""
        @ClassHook
        class MyClass:
            def __init__(self):
                self.x = 1

        obj1 = MyClass()
        obj2 = MyClass()

        obj1.modify(lambda inst: setattr(inst, 'x', 999))
        assert obj1.x == 999
        assert obj2.x == 1

    def test_multiple_deferred_hooks(self):
        """Multiple modify() calls apply in order."""
        @ClassHook
        class MyClass:
            def __init__(self):
                self.log = []

        MyClass.modify(lambda inst: inst.log.append("first"))
        MyClass.modify(lambda inst: inst.log.append("second"))

        obj = MyClass()
        assert obj.log == ["first", "second"]

    def test_getattr_delegation(self):
        """ClassHook delegates attribute access to the wrapped class."""
        @ClassHook
        class MyClass:
            class_var = "hello"
            def __init__(self):
                pass

        assert MyClass.class_var == "hello"
