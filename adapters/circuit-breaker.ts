// ---------------------------------------------------------------------------
// Circuit Breaker for Bedrock API calls
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export enum CircuitState {
  CLOSED = 'CLOSED',
  OPEN = 'OPEN',
  HALF_OPEN = 'HALF_OPEN',
}

// ---------------------------------------------------------------------------
// Error
// ---------------------------------------------------------------------------

export class CircuitBreakerOpenError extends Error {
  constructor(message = 'Circuit breaker is open — request rejected') {
    super(message);
    this.name = 'CircuitBreakerOpenError';
  }
}

// ---------------------------------------------------------------------------
// Logger (structured JSON, matching other adapters)
// ---------------------------------------------------------------------------

const logger = {
  info: (msg: string, meta?: Record<string, unknown>) =>
    console.log(JSON.stringify({ level: 'info', msg, ...meta })),
  warn: (msg: string, meta?: Record<string, unknown>) =>
    console.warn(JSON.stringify({ level: 'warn', msg, ...meta })),
};

// ---------------------------------------------------------------------------
// Implementation
// ---------------------------------------------------------------------------

export class CircuitBreaker {
  private state: CircuitState = CircuitState.CLOSED;
  private failures: number[] = []; // timestamps of consecutive failures
  private openedAt: number | null = null;

  private readonly failureThreshold: number;
  private readonly resetTimeout: number;
  private readonly windowMs: number;

  constructor(
    options: {
      failureThreshold?: number;
      resetTimeout?: number;
      windowMs?: number;
    } = {},
  ) {
    this.failureThreshold = options.failureThreshold ?? 5;
    this.resetTimeout = options.resetTimeout ?? 30_000;
    this.windowMs = options.windowMs ?? 60_000;
  }

  /** Current circuit state. */
  getState(): CircuitState {
    return this.state;
  }

  /**
   * Execute an async function through the circuit breaker.
   *
   * - CLOSED: call goes through; consecutive failures within the time window
   *   are tracked. After `failureThreshold` failures the circuit opens.
   * - OPEN: rejects immediately with `CircuitBreakerOpenError` until the
   *   `resetTimeout` cooldown elapses, then transitions to HALF_OPEN.
   * - HALF_OPEN: allows one probe call. Success → CLOSED, failure → OPEN.
   */
  async execute<T>(fn: () => Promise<T>): Promise<T> {
    const now = Date.now();

    if (this.state === CircuitState.OPEN) {
      if (this.openedAt !== null && now - this.openedAt >= this.resetTimeout) {
        this.transitionTo(CircuitState.HALF_OPEN);
      } else {
        throw new CircuitBreakerOpenError();
      }
    }

    try {
      const result = await fn();
      this.onSuccess();
      return result;
    } catch (err) {
      this.onFailure(now);
      throw err;
    }
  }

  // -----------------------------------------------------------------------
  // Internal helpers
  // -----------------------------------------------------------------------

  private onSuccess(): void {
    if (this.state === CircuitState.HALF_OPEN || this.state === CircuitState.CLOSED) {
      this.failures = [];
      this.openedAt = null;
      this.transitionTo(CircuitState.CLOSED);
    }
  }

  private onFailure(now: number): void {
    if (this.state === CircuitState.HALF_OPEN) {
      this.transitionTo(CircuitState.OPEN);
      this.openedAt = now;
      return;
    }

    // Prune failures outside the sliding window
    this.failures = this.failures.filter((t) => now - t < this.windowMs);
    this.failures.push(now);

    if (this.failures.length >= this.failureThreshold) {
      this.transitionTo(CircuitState.OPEN);
      this.openedAt = now;
    }
  }

  private transitionTo(next: CircuitState): void {
    if (this.state !== next) {
      logger.info('CircuitBreaker state change', { from: this.state, to: next });
      this.state = next;
    }
  }
}
