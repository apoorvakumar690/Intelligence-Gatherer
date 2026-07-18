#!/usr/bin/env python3
"""
Tracer Utility for Langfuse Observability
=========================================
Provides reusable, zero-friction tracing and monitoring context managers
and wrappers that work with Langfuse if configured, and fall back to
graceful, silent no-ops if not.

Auto-installs 'langfuse' package if credentials are found but the library is missing.
"""

import os
import sys
import time
import subprocess
from contextlib import contextmanager

# ─── Auto-install Helper ──────────────────────────────────────────────────────

def _auto_install_langfuse() -> None:
    print("Installing langfuse package for observability...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", "langfuse"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("langfuse installed successfully.")
    except Exception as e:
        print(f"WARN: Failed to auto-install langfuse: {e}", file=sys.stderr)

# Check credentials in environment or dotenv (dotenv should be loaded before importing tracer)
HAS_KEYS = bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))

HAS_LANGFUSE = False
if HAS_KEYS:
    try:
        import langfuse
        HAS_LANGFUSE = True
    except ImportError:
        # Try to auto-install once
        _auto_install_langfuse()
        try:
            import langfuse
            HAS_LANGFUSE = True
        except ImportError:
            pass

# Cache client instance
_langfuse_client = None

def get_langfuse_client():
    """Get or initialize the Langfuse client if configured."""
    global _langfuse_client
    if not HAS_LANGFUSE:
        return None
    if _langfuse_client is None:
        try:
            from langfuse import Langfuse
            # Automatically picks up LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
            _langfuse_client = Langfuse()
        except Exception as e:
            print(f"WARN: Failed to initialize Langfuse client: {e}", file=sys.stderr)
            return None
    return _langfuse_client

# ─── Traced OpenAI Client Wrapper ─────────────────────────────────────────────

class TracedCompletions:
    """Wrapper around chat.completions to inject trace_id and parent_observation_id."""
    def __init__(self, base_completions, trace_obj=None, span_obj=None):
        self.base_completions = base_completions
        self.trace_obj = trace_obj
        self.span_obj = span_obj

    def create(self, **kwargs):
        # Identify if we are using the Langfuse-wrapped OpenAI client completions
        is_langfuse_client = HAS_LANGFUSE and "langfuse" in str(type(self.base_completions))

        # Check if caller passed trace_id / parent_observation_id directly
        trace_id = kwargs.pop("trace_id", None)
        parent_observation_id = kwargs.pop("parent_observation_id", None)

        # Fall back to default trace/span tied to this client instance
        if not trace_id and self.trace_obj and hasattr(self.trace_obj, "id"):
            if self.trace_obj.id != "mock-trace-id":
                trace_id = self.trace_obj.id
        if not parent_observation_id and self.span_obj and hasattr(self.span_obj, "id"):
            if self.span_obj.id != "mock-span-id":
                parent_observation_id = self.span_obj.id

        if is_langfuse_client:
            if trace_id:
                kwargs["trace_id"] = trace_id
            if parent_observation_id:
                kwargs["parent_observation_id"] = parent_observation_id
        else:
            # Strip key-value arguments that standard OpenAI client rejects
            kwargs.pop("trace_id", None)
            kwargs.pop("parent_observation_id", None)

        return self.base_completions.create(**kwargs)


class TracedChat:
    """Wrapper around client.chat."""
    def __init__(self, base_chat, trace_obj=None, span_obj=None):
        self.completions = TracedCompletions(base_chat.completions, trace_obj, span_obj)


class TracedOpenAI:
    """Delegating wrapper for the OpenAI client to support effortless trace binding."""
    def __init__(self, base_client, trace_obj=None, span_obj=None):
        self.base_client = base_client
        self.trace_obj = trace_obj
        self.span_obj = span_obj
        self.chat = TracedChat(base_client.chat, trace_obj, span_obj)

    def bind_context(self, trace_obj=None, span_obj=None, clear_span=False):
        """Binds a trace or span context to this client instance for future completions."""
        if trace_obj is not None:
            self.trace_obj = trace_obj
        if clear_span:
            self.span_obj = None
        elif span_obj is not None:
            self.span_obj = span_obj
        self.chat = TracedChat(self.base_client.chat, self.trace_obj, self.span_obj)

    def __getattr__(self, name):
        return getattr(self.base_client, name)


def get_openai_client(api_key: str, base_url: str, trace_obj=None, span_obj=None):
    """
    Returns an OpenAI-like client.
    If Langfuse is active, wraps completions in LangfuseOpenAI for automatic trace logging.
    If Langfuse is disabled, returns a standard OpenAI client (wrapped to silently ignore trace params).
    """
    if HAS_LANGFUSE and get_langfuse_client() is not None:
        try:
            from langfuse.openai import OpenAI as LangfuseOpenAI
            lf_client = LangfuseOpenAI(api_key=api_key, base_url=base_url)
            return TracedOpenAI(lf_client, trace_obj, span_obj)
        except Exception as e:
            print(f"WARN: Failed to wrap OpenAI client with Langfuse: {e}", file=sys.stderr)

    from openai import OpenAI
    std_client = OpenAI(api_key=api_key, base_url=base_url)
    return TracedOpenAI(std_client, trace_obj, span_obj)

# ─── Mock Fallbacks ───────────────────────────────────────────────────────────

class MockTrace:
    def __init__(self):
        self.id = "mock-trace-id"
    def update(self, *args, **kwargs): pass
    def span(self, *args, **kwargs): return MockSpan()

class MockSpan:
    def __init__(self):
        self.id = "mock-span-id"
    def update(self, *args, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): pass

# ─── Context Managers ─────────────────────────────────────────────────────────

@contextmanager
def trace(name: str, input_data: dict | None = None, metadata: dict | None = None):
    """
    Start a trace. Context manager yield a Trace object (real or mock).
    Usage:
        with tracer.trace("workflow_name", {"url": url}) as my_trace:
            ...
    """
    lf = get_langfuse_client()
    
    if lf:
        try:
            t = lf.trace(
                name=name,
                input=input_data,
                metadata=metadata
            )
            yield t
            t.update(output={"status": "success"})
            lf.flush()
            return
        except Exception as e:
            print(f"WARN: Error during Langfuse trace setup: {e}", file=sys.stderr)
            
    # Mock trace fallback
    yield MockTrace()


@contextmanager
def span(parent_context, name: str, input_data: dict | None = None, metadata: dict | None = None):
    """
    Start a nested span under a parent trace or span.
    Usage:
        with tracer.span(my_trace, "fetch_step") as my_span:
            ...
    """
    lf_span = None
    if HAS_LANGFUSE and parent_context and hasattr(parent_context, "span") and parent_context.id != "mock-trace-id":
        try:
            lf_span = parent_context.span(
                name=name,
                input=input_data,
                metadata=metadata
            )
        except Exception as e:
            print(f"WARN: Failed to create Langfuse span '{name}': {e}", file=sys.stderr)

    if lf_span:
        try:
            yield lf_span
            lf_span.update(output={"status": "success"})
        except Exception as exc:
            lf_span.update(output={"status": "error", "error": str(exc)})
            raise exc
    else:
        yield MockSpan()
