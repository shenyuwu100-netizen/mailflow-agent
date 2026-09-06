"""
Retry Handler with Circuit Breaker

Implements retry logic with exponential backoff and circuit breaker pattern
to handle API failures gracefully and prevent cascading failures.
"""

import time
import random
from typing import Callable, Any, Optional, Dict, List
from enum import Enum
from datetime import datetime, timedelta
from functools import wraps
from utils.logger import get_logger


logger = get_logger('retry_handler')


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open."""
    pass


class CircuitBreaker:
    """Circuit breaker to prevent cascading failures."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception
    ):
        """
        Initialize CircuitBreaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before attempting recovery
            expected_exception: Exception type to catch
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state = CircuitState.CLOSED

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Call function through circuit breaker.

        Args:
            func: Function to call
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Function result

        Raises:
            CircuitBreakerError: If circuit is open
        """
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker entering HALF_OPEN state", {
                    'failure_count': self.failure_count
                })
            else:
                raise CircuitBreakerError(
                    f"Circuit breaker is OPEN. "
                    f"Retry after {self.recovery_timeout}s"
                )

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise e

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self.last_failure_time is None:
            return True
        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.recovery_timeout

    def _on_success(self):
        """Handle successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            logger.info("Circuit breaker CLOSED (recovered)", {
                'state': self.state.value
            })

    def _on_failure(self):
        """Handle failed call."""
        self.failure_count += 1
        self.last_failure_time = datetime.now()

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.error("Circuit breaker OPEN", {
                'failure_count': self.failure_count,
                'threshold': self.failure_threshold
            })

    def reset(self):
        """Manually reset circuit breaker."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        logger.info("Circuit breaker manually reset")

    def get_state(self) -> Dict[str, Any]:
        """Get circuit breaker state."""
        return {
            'state': self.state.value,
            'failure_count': self.failure_count,
            'last_failure_time': self.last_failure_time.isoformat() if self.last_failure_time else None,
            'failure_threshold': self.failure_threshold,
            'recovery_timeout': self.recovery_timeout
        }


class RetryHandler:
    """Handles retry logic with exponential backoff and jitter."""

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        circuit_breaker: Optional[CircuitBreaker] = None
    ):
        """
        Initialize RetryHandler.

        Args:
            max_retries: Maximum number of retry attempts
            base_delay: Base delay in seconds
            max_delay: Maximum delay in seconds
            exponential_base: Base for exponential backoff
            jitter: Whether to add random jitter
            circuit_breaker: Optional circuit breaker
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.circuit_breaker = circuit_breaker

    def execute(
        self,
        func: Callable,
        *args,
        retryable_exceptions: tuple = (Exception,),
        on_retry: Optional[Callable] = None,
        **kwargs
    ) -> Any:
        """
        Execute function with retry logic.

        Args:
            func: Function to execute
            *args: Positional arguments
            retryable_exceptions: Exceptions that trigger retry
            on_retry: Callback function called on each retry
            **kwargs: Keyword arguments

        Returns:
            Function result

        Raises:
            Last exception if all retries fail
        """
        last_exception = None
        attempt = 0

        while attempt <= self.max_retries:
            try:
                # Use circuit breaker if available
                if self.circuit_breaker:
                    return self.circuit_breaker.call(func, *args, **kwargs)
                else:
                    return func(*args, **kwargs)

            except retryable_exceptions as e:
                last_exception = e
                attempt += 1

                if attempt > self.max_retries:
                    logger.error(
                        f"All retry attempts exhausted",
                        {
                            'function': func.__name__,
                            'attempts': attempt,
                            'error': str(e)
                        },
                        exc_info=True
                    )
                    raise e

                # Calculate delay
                delay = self._calculate_delay(attempt)

                logger.warning(
                    f"Retry attempt {attempt}/{self.max_retries}",
                    {
                        'function': func.__name__,
                        'attempt': attempt,
                        'delay': delay,
                        'error': str(e)
                    }
                )

                # Call retry callback if provided
                if on_retry:
                    on_retry(attempt, delay, e)

                # Wait before retry
                time.sleep(delay)

            except CircuitBreakerError as e:
                logger.error(
                    "Circuit breaker is open, not retrying",
                    {
                        'function': func.__name__,
                        'error': str(e)
                    }
                )
                raise e

        # Should not reach here, but just in case
        if last_exception:
            raise last_exception

    def _calculate_delay(self, attempt: int) -> float:
        """
        Calculate delay for retry attempt with exponential backoff and jitter.

        Args:
            attempt: Current attempt number (1-indexed)

        Returns:
            Delay in seconds
        """
        # Exponential backoff
        delay = self.base_delay * (self.exponential_base ** (attempt - 1))

        # Cap at max delay
        delay = min(delay, self.max_delay)

        # Add jitter to prevent thundering herd
        if self.jitter:
            jitter_amount = delay * 0.1  # 10% jitter
            delay += random.uniform(-jitter_amount, jitter_amount)

        return max(0, delay)


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple = (Exception,),
    circuit_breaker: Optional[CircuitBreaker] = None
):
    """
    Decorator to add retry logic to a function.

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds
        exponential_base: Base for exponential backoff
        jitter: Whether to add random jitter
        retryable_exceptions: Exceptions that trigger retry
        circuit_breaker: Optional circuit breaker

    Returns:
        Decorated function
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            handler = RetryHandler(
                max_retries=max_retries,
                base_delay=base_delay,
                max_delay=max_delay,
                exponential_base=exponential_base,
                jitter=jitter,
                circuit_breaker=circuit_breaker
            )
            return handler.execute(
                func,
                *args,
                retryable_exceptions=retryable_exceptions,
                **kwargs
            )
        return wrapper
    return decorator


# Global circuit breakers for different services
_circuit_breakers: Dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: int = 60
) -> CircuitBreaker:
    """
    Get or create a circuit breaker.

    Args:
        name: Circuit breaker name
        failure_threshold: Number of failures before opening
        recovery_timeout: Seconds to wait before recovery

    Returns:
        CircuitBreaker instance
    """
    if name not in _circuit_breakers:
        _circuit_breakers[name] = CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout
        )
    return _circuit_breakers[name]


def get_all_circuit_breakers() -> Dict[str, Dict[str, Any]]:
    """Get state of all circuit breakers."""
    return {
        name: breaker.get_state()
        for name, breaker in _circuit_breakers.items()
    }
