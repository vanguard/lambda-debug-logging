import functools
import logging
import random
from enum import Enum

from .formatters import JSONFormatter
from .handlers import DebugBufferHandler, LevelFilterHandler


_BUFFER_HANDLER = None


def register_handler(stream=None, formatter=None):
    """Register the Logging handlers"""
    global _BUFFER_HANDLER  # pylint: disable=global-statement
    log = logging.getLogger("lambda_debug_logging")
    log.debug("Registering logging handlers")

    for name in ["botocore", "aws_xray_sdk"]:
        # Prevent certain libraries from doing debug logging
        logging.getLogger(name).setLevel(logging.INFO)

    # Remove any existing handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    # Use a Stream handler
    stream_handler = logging.StreamHandler(stream)

    # Format using json by default
    if formatter is None:
        formatter = JSONFormatter()
    stream_handler.setFormatter(formatter)

    # The realtime handler is for >=INFO
    # All these messages go right to the stream handler
    realtime_handler = LevelFilterHandler(
        min_level=logging.INFO, max_level=logging.CRITICAL, target=stream_handler
    )

    # The debug handler is for ==DEBUG
    # All messages go into the buffer
    # If an ERROR is written to the buffer, then the buffer will write all buffered
    # messages to the LevelFilterHandler.  The LevelFilterHandler will only allow DEBUG
    # though.  This prevents duplicate messages, but does have DEBUG show up after >= INFO
    debug_only_handler = LevelFilterHandler(
        min_level=logging.DEBUG, max_level=logging.DEBUG, target=stream_handler
    )
    _BUFFER_HANDLER = DebugBufferHandler(target=debug_only_handler)

    # Add the handlers, but set the log level to DEBUG
    root_logger.addHandler(realtime_handler)
    root_logger.addHandler(_BUFFER_HANDLER)
    root_logger.setLevel(logging.DEBUG)


def clear_buffer(sample_rate: float = 0.001):
    """Clear the debug buffer"""
    global _BUFFER_HANDLER  # pylint: disable=global-statement
    if _BUFFER_HANDLER is None:
        raise Exception(
            "register_handler() was never called.  It must be called before using the logging library"
        )

    r: float = random.random()
    if r <= sample_rate and len(_BUFFER_HANDLER.buffer) > 0:
        log = logging.getLogger("lambda_debug_logging")
        items = len(_BUFFER_HANDLER.buffer)
        log.info(
            f"Writing debug statements, r={r}, sample_rate={sample_rate}, messages={items}"
        )
        _BUFFER_HANDLER.flush()

    _BUFFER_HANDLER.clear()


class LambdaResponseType(Enum):
    """Enumeration of Lambda response types"""

    UNKNOWN = 0
    HTTP = 1
    APIGW_AUTHPOLICY = 2


def lambda_debug_logging(
    response_type: LambdaResponseType = LambdaResponseType.UNKNOWN,
    sample_rate: float = 0.001,
):
    """Decorator that writes debug logs if there is a need to"""

    # The two levels of nested functions is needed to allow a decorator
    # that has it's own arguments
    def top_decorator(func):

        # functools "fixes" the name of the decorated function
        @functools.wraps(func)
        def wrapper(event, context):

            # delegate most of the logic to another function
            return _wrapper_handler(
                func,
                event,
                context,
                response_type=response_type,
                sample_rate=sample_rate,
            )

        return wrapper

    return top_decorator


def _wrapper_handler(
    func,
    event,
    context,
    response_type: LambdaResponseType = LambdaResponseType.UNKNOWN,
    sample_rate: float = 0.001,
):

    log = logging.getLogger("lambda_debug_logging")
    resp = None
    try:
        resp = func(event, context)
    except Exception as exception:
        log.exception("Failed execution")
        raise exception

    try:
        if response_type == LambdaResponseType.HTTP:
            if resp is None:
                log.error("No response was given")
            else:
                status_code = resp.get("statusCode", 200)
                if status_code >= 400:
                    log.error("Status Code: %s", status_code)
        elif response_type == LambdaResponseType.APIGW_AUTHPOLICY:
            effect = (
                resp.get("policyDocument", {})
                .get("Statement", [{}])[0]
                .get("Effect", "Deny")
            )
            if effect == "Deny":
                log.error("Auth was denied")
    except Exception as exception:  # pylint: disable=broad-except
        # post-execution exceptions shouldn't fail the overall execution
        log.exception("Failed post-execution: %s", str(exception))

    clear_buffer(sample_rate=sample_rate)

    return resp
