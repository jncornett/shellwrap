import os
import sys

from collections import namedtuple
from subprocess import Popen, PIPE, CalledProcessError
from threading import Thread, Event, Lock
from time import sleep

Arguments = namedtuple("Arguments", ["args", "kwargs"])

# ## Utility Functions

# ### pluck
# Pops the specified `keys` and corresponding values from the dictionary `d`
# and returns them as a new dictionary.
# 
# Example:
#
#     d = {"A": 1, "B": 2, "C": 3}
#     e = pluck(d, "A", "D")
#
#     print(e) # prints "{'A': 1}"
#     print(d) # prints "{'B': 2, 'C': 3}"
#
def pluck(d, *keys):
    _d = {}
    for k in keys:
        if k in d:
            _d[k] = d.pop(k)

    return _d


# ### remap
# Creates a new dictionary based on `d` with the mapping `mapping`.
# `mapping` can be either a dictionary or a callable.
# 
# Example:
#
#     d = {"A": 1, "B": 2}
#     e = remap(d, {"A": "C"})
#    
#     print(e) # prints "{'C': 1, 'B': 2}"
#
#     f = remap(d, str.lower)
#     print(f) # prints "{'a': 1, 'b': 2}"
def remap(d, mapping):
    if hasattr(mapping, "get"):
        return {mapping.get(k, k): v for k, v in d.items()}
    else:
        return {mapping(k) or k: v for k, v in d.items()}


# Strips the leading underscore on a string
def _strip_underscore(string):
    return string.lstrip("_")


# ## ProcessError
# Custom exception for SubprocessHelper errors accepts an instance of
# `ProcessWrapper`. ProcessError extracts relevant data from the process and 
# stores it in the `data` attribute.
#
# The data attribute contains the following fields:
# - `returncode` the returncode from the underlying process
# - `cmdline` the command line that the process was called with
# - `stderr` the STDERR output of the process
# If the process was wrapped with `ProcessWrapperWithTimeout`, two additional
# fields are available:
# - `timeout` the timeout value in seconds
# - `timed_out` `False` if the process exited normally, `True` if the process was
#     terminated by `ProcessWrapperWithTimeout`
class ProcessError(Exception):

    def __init__(self, proc):
        self.data = {
            "returncode": proc.returncode,
            "cmdline": proc.cmdline,
            "stderr": proc.stderr.read()
            }

        if hasattr(proc, "timeout"):
            self.data.update(timeout=proc.timeout,
                             timed_out=proc.timed_out)
            msg = "Process timed out after {} seconds".format(self.data["timeout"])
        else:
            msg = "Process exited with code {}".format(self.data["returncode"])

        super(ProcessError, self).__init__(msg)


# ## Process Wrapper
# A thin wrapper around the `Popen` object returned by `subprocess` calls.
# This class is initialized with a `Popen` object and the `cmdline` (command line)
# that was used to invoke it.
class ProcessWrapper(object):

    def __init__(self, proc, cmdline):
        self.proc = proc
        self.cmdline = cmdline


    def __getattr__(self, attr):
        return getattr(self.proc, attr)

    
    # ### check
    # Waits for the process to exit and raises a `ProcessError` if
    # the process returned an exit code other than `0`
    def check(self):
        self.wait()
        if self.returncode:
            raise ProcessError(self)


# ## Process Wrapper With Timeout
# Provides timeout facilities to `ProcessWrapper` in the form of a sentinel thread.
# Accepts the same arguments as `ProcessWrapper` with the addition of a `timeout`
# argument. `timeout` is an integer value representing the number of seconds to wait
# until terminating the subprocess.
class ProcessWrapperWithTimeout(Thread, ProcessWrapper):

    def __init__(self, proc, cmdline, timeout):
        Thread.__init__(self)
        ProcessWrapper.__init__(self, proc, cmdline)
        self._timeout_event = Event()
        self.timeout = timeout
        self.daemon = True
        self.start()

    
    # #### timed_out
    # `False` if the process is still running, or if it exited normally.
    # `True` if the process timed out and the sentinel thread attempted to terminate it.
    # _Note: Since the sentinel thread does not `wait()` for the process to complete
    # before setting the `timed_out` property, it is possible that `timed_out` will
    # be `False` for a short period even before the subprocess has exited.
    @property
    def timed_out(self):
        return self.proc.poll() is not None and self._timeout_event.is_set()


    def run(self):
       sleep(self.timeout)
       if self.poll() is None:
           self._timeout_event.set()
           self.terminate()


# ## SubprocessHelper
# A wrapper for subprocesses.
class SubprocessHelper(object):

    DEFAULT_OPTIONS = {
        "stderr": PIPE,
        "stdout": PIPE
    }


    def __init__(self, command, options, arguments):
        self.command = command
        self.options = options
        self.arguments = list(arguments)


    @classmethod
    def _get_options(cls, kwargs, defaults=None):
        return dict(
                defaults or cls.DEFAULT_OPTIONS,
                **remap(pluck(kwargs, "_cwd", "_env", "_timeout"), _strip_underscore))


    # ### create
    # Constructor for `SubprocessHelper`
    #
    # Example:
    #
    #     sh = SubprocessHelper.create("ls", "*.py", l=True)
    #     print(sh.cmdline) # prints "['ls', '-l', '*.py']
    @classmethod
    def create(cls, command, *args, **kwargs):
        return cls(command, cls._get_options(kwargs), [Arguments(args, kwargs)])

    
    # ### add
    # Add additional positional and keyword arguments to the helper
    def add(self, *args, **kwargs):
        top = self.arguments[-1]
        self.options = self._get_options(kwargs, self.options)
        self.arguments[-1] = Arguments(top.args + args, dict(top.kwargs, **kwargs))


    # ### bake
    # Allows the extension of `SubprocessHelper` objects with subcommands. This 
    # method exists so that keyword arguments are properly located _after_ their
    # respective subcommands.
    # 
    # Example:
    #
    #     sh = SubprocessHelper.create("cvs", n=True, q=True)
    #     sh.add("update", C=True)
    #     print(sh.cmdline) # prints "['cvs', '-n', '-q', '-C', 'update']"
    #     
    #     sh = SubprocessHelper.create("cvs", n=True, q=True)
    #     sh2 = sh.bake("update").bake(C=True)
    #     print(sh2.cmdline) # prints "['cvs', '-n', '-q', 'update', '-C']"
    def bake(self, *args, **kwargs):
        i = self.__class__(
                self.command, 
                self._get_options(kwargs, self.options),
                self.arguments)

        if args or kwargs:
            i.arguments.append(Arguments(args, kwargs))

        return i

    
    # ### copy
    def copy(self):
        return self.bake()


    # ### subcommand
    # Allows the addition of a subcommand
    def subcommand(self, command, *args, **kwargs):
        sub = self.__class__.create(command, *args, **kwargs)
        i = self.copy()
        i.arguments.append(sub)
        return i


    # ### resolve_parameter
    @staticmethod
    def _resolve_parameter(param):
        if param.startswith("__"):
            return "--{}".format(param[2:])
        elif param.startswith("_"):
            return "-{}".format(param[1:])
        elif len(param) == 1:
            return "-{}".format(param)
        else:
            return "--{}".format(param)


    # ### get_cmdline
    @classmethod
    def _get_cmdline(cls, command, arguments):
        cmdline = [command]
        for obj in arguments:
            if isinstance(obj, Arguments):
                for key, value in obj.kwargs.items():
                    if value is not None:
                        param = cls._resolve_parameter(key)
                        cmdline.append(param)
                        if value is not True:
                            cmdline.append(str(value))

                cmdline.extend(obj.args)
            elif isinstance(obj, cls):
                cmdline.extend(obj.cmdline)

        return cmdline

    
    # ### cmdline
    @property
    def cmdline(self):
        return self._get_cmdline(self.command, self.arguments)


    # ### call
    def call(self, *args, **kwargs):
        options = self._get_options(kwargs, self.options)
        cmdline = self._get_cmdline(
                self.command,
                self.arguments + [Arguments(args, kwargs)])

        timeout = options.pop("timeout", None)
        if timeout:
            return ProcessWrapperWithTimeout(
                    Popen(cmdline, **options),
                    cmdline,
                    timeout)
        else:
            return ProcessWrapper(
                    Popen(cmdline, **options),
                    cmdline)
