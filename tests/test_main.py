from unittest import TestCase
from shellwrap import *

from collections import namedtuple
from io import BytesIO
import subprocess

ProcessWrapperDummy = namedtuple("ProcessWrapperDummy", 
                                  ["returncode", "cmdline", "stderr"])
ProcessWrapperTimeoutDummy = namedtuple("ProcessWrapperDummy", 
                              [
                                  "returncode", 
                                  "cmdline", 
                                  "stderr", 
                                  "timeout", 
                                  "timed_out"
                              ])

ProcessDummy = namedtuple("ProcessDummy",
        ["stderr", "wait", "poll", "returncode"])

noop = lambda *a, **kw: None

def call_sleep(timeout):
    return subprocess.Popen(
            ["sleep", str(timeout)],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE)

class TestProcessError(TestCase):
    def setUp(self):
        self.proc_dummy = ProcessWrapperDummy(
                2, ["ls", "something"], BytesIO("No such foo"))
        self.proc_dummy_with_timeout = ProcessWrapperTimeoutDummy(
                -15, ["sleep", "1000000"], BytesIO(""), 10, True)


    def test_process_wrapper(self):
        try:
            raise ProcessError(self.proc_dummy)
        except ProcessError as e:
            pass

        self.assertDictEqual(e.data, {
            "returncode": 2,
            "cmdline": ["ls", "something"],
            "stderr": "No such foo",
            })

        self.assertEqual(e.message, "Process exited with code 2")

    def test_process_wrapper_with_timeout(self):
        e = ProcessError(self.proc_dummy_with_timeout)
        self.assertDictContainsSubset({"timeout": 10, "timed_out": True}, e.data)
        self.assertEqual(e.message, "Process timed out after 10 seconds")


class TestProcessWrapper(TestCase):
    def setUp(self):
        self.proc_dummy_ok = ProcessDummy(BytesIO("A\nB\nC"), noop, noop, 0)
        self.proc_dummy_err = ProcessDummy(BytesIO("A\nB\nC"), noop, noop, 2)

    def test_ok(self):
        p = ProcessWrapper(self.proc_dummy_ok, ["ls", "something"])
        self.assertEqual(p.returncode, 0)
        self.assertIsNone(p.wait())
        p.check()

    def test_error(self):
        p = ProcessWrapper(self.proc_dummy_err, ["ls", "something"])
        self.assertRaises(ProcessError, p.check)


class TestProcessWrapperWithTimeout(TestCase):
    def setUp(self):
        self.short_timeout = 0.05
        self.long_timeout = 0.1

    def test_no_timeout(self):
        p = ProcessWrapperWithTimeout(
                call_sleep(self.short_timeout),
                ["foo"],
                self.long_timeout)

        p.check()

    def test_with_timeout(self):
        p = ProcessWrapperWithTimeout(
                call_sleep(self.long_timeout),
                ["foo"],
                self.short_timeout)

        self.assertRaises(ProcessError, p.check)


class TestSubprocessHelper(TestCase):
    def test_resolve_parameter(self):
        rv = map(SubprocessHelper._resolve_parameter, 
                ["a", "all", "_a", "_ab", "__a", "__all"])

        self.assertEquals(list(rv), 
                ["-a", "--all", "-a", "-ab", "--a", "--all"])

    def test_get_options(self):
        kwargs = {"foo": "1", "D": "2", "_cwd": "/usr/local/bin",
                "_timeout": 2, "_env": {"HOME": "/root"}}

        self.assertEquals(SubprocessHelper._get_options(kwargs),
                {
                    "stderr": subprocess.PIPE,
                    "stdout": subprocess.PIPE,
                    "cwd": "/usr/local/bin",
                    "timeout": 2,
                    "env": {"HOME": "/root"}
                })

        self.assertEquals(kwargs, {"foo": "1", "D": "2"})
        self.assertDictContainsSubset({"bar": "2"}, 
                SubprocessHelper._get_options(kwargs, {"bar": "2"}))


    def test_create(self):
        sub = SubprocessHelper.create("ls", "one", F=True, _cwd="bar", _timeout=1)
        self.assertEqual(sub.cmdline, ["ls", "-F", "one"])

    def test_get_cmdline(self):
        sub = SubprocessHelper.create("bar", "one", "two", three="4")
        arguments = [
                Arguments((), {}),
                Arguments(("A", "B"), {"C": "3"}),
                sub,
                Arguments((), {})
                ]

        self.assertEquals(SubprocessHelper._get_cmdline("foo", arguments),
                ["foo", "-C", "3", "A", "B", "bar", "--three", "4", "one", "two"])

    def test_add(self):
        sub = SubprocessHelper.create("ls", "one", _cwd="foo")
        sub.add("two", g=True)

        self.assertEquals(sub.cmdline, ["ls", "-g", "one", "two"])
        self.assertDictContainsSubset({"cwd": "foo"}, sub.options)
    
    def test_bake(self):
        sub = SubprocessHelper.create("foo", "one", L=True)
        sub2 = sub.bake("three", F=True)
        self.assertEquals(sub2.cmdline, ["foo", "-L", "one", "-F", "three"])
        self.assertIsNot(sub, sub2)

    def test_copy(self):
        sub = SubprocessHelper.create("foo", "one", L=True)
        self.assertIsNot(sub, sub.copy())

    def test_subcommand(self):
        sub = SubprocessHelper.create("cvs", n=True, q=True)
        up = sub.subcommand("update", C=True)
        self.assertEquals(up.cmdline, ["cvs", "-q", "-n", "update", "-C"])

    def test_call(self):
        sub = SubprocessHelper.create("ls")
        p = sub.call(l=True)

        self.assertIsInstance(p, ProcessWrapper)
        self.assertNotIsInstance(p, ProcessWrapperWithTimeout)
        p.check()
        self.assertEqual(p.returncode, 0)
        self.assertTrue(p.stdout.read())

    def test_call_with_error(self):
        sub = SubprocessHelper.create("ls", "doesntexistfffff")
        p = sub.call()
        self.assertRaises(ProcessError, p.check)
        self.assertNotEqual(p.returncode, 0)

    def test_call_with_timeout(self):
        sub = SubprocessHelper.create("sleep", _timeout=0.1)
        p1 = sub.call("0.05")
        p1.check()
        self.assertEqual(p1.returncode, 0)

        p2 = sub.call("0.15")
        self.assertRaises(ProcessError, p2.check)

