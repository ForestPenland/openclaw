import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  CircuitBreaker,
  CircuitBreakerOpenError,
  CircuitState,
} from '../../../adapters/circuit-breaker.ts';

describe('CircuitBreaker', () => {
  let consoleSpy: ReturnType<typeof vi.spyOn>;
  let consoleWarnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    consoleSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
    consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.useFakeTimers();
  });

  afterEach(() => {
    consoleSpy.mockRestore();
    consoleWarnSpy.mockRestore();
    vi.useRealTimers();
  });

  it('starts in CLOSED state', () => {
    const cb = new CircuitBreaker();
    expect(cb.getState()).toBe(CircuitState.CLOSED);
  });

  it('passes through successful calls in CLOSED state', async () => {
    const cb = new CircuitBreaker();
    const result = await cb.execute(() => Promise.resolve(42));
    expect(result).toBe(42);
    expect(cb.getState()).toBe(CircuitState.CLOSED);
  });

  it('stays CLOSED when failures are below threshold', async () => {
    const cb = new CircuitBreaker({ failureThreshold: 5 });

    for (let i = 0; i < 4; i++) {
      await expect(cb.execute(() => Promise.reject(new Error('fail')))).rejects.toThrow('fail');
    }

    expect(cb.getState()).toBe(CircuitState.CLOSED);
  });

  it('opens after failureThreshold consecutive failures within window', async () => {
    const cb = new CircuitBreaker({ failureThreshold: 3, windowMs: 60_000 });

    for (let i = 0; i < 3; i++) {
      await expect(cb.execute(() => Promise.reject(new Error('fail')))).rejects.toThrow('fail');
    }

    expect(cb.getState()).toBe(CircuitState.OPEN);
  });

  it('rejects immediately with CircuitBreakerOpenError when OPEN', async () => {
    const cb = new CircuitBreaker({ failureThreshold: 1 });

    await expect(cb.execute(() => Promise.reject(new Error('fail')))).rejects.toThrow('fail');
    expect(cb.getState()).toBe(CircuitState.OPEN);

    await expect(cb.execute(() => Promise.resolve('ok'))).rejects.toThrow(CircuitBreakerOpenError);
  });

  it('transitions to HALF_OPEN after resetTimeout elapses', async () => {
    const cb = new CircuitBreaker({ failureThreshold: 1, resetTimeout: 5000 });

    await expect(cb.execute(() => Promise.reject(new Error('fail')))).rejects.toThrow();
    expect(cb.getState()).toBe(CircuitState.OPEN);

    vi.advanceTimersByTime(5000);

    // Next call should be allowed (HALF_OPEN probe)
    const result = await cb.execute(() => Promise.resolve('recovered'));
    expect(result).toBe('recovered');
    expect(cb.getState()).toBe(CircuitState.CLOSED);
  });

  it('returns to OPEN if HALF_OPEN probe fails', async () => {
    const cb = new CircuitBreaker({ failureThreshold: 1, resetTimeout: 5000 });

    await expect(cb.execute(() => Promise.reject(new Error('fail')))).rejects.toThrow();
    expect(cb.getState()).toBe(CircuitState.OPEN);

    vi.advanceTimersByTime(5000);

    await expect(cb.execute(() => Promise.reject(new Error('still broken')))).rejects.toThrow(
      'still broken',
    );
    expect(cb.getState()).toBe(CircuitState.OPEN);
  });

  it('resets failure count on success in CLOSED state', async () => {
    const cb = new CircuitBreaker({ failureThreshold: 3, windowMs: 60_000 });

    // 2 failures
    await expect(cb.execute(() => Promise.reject(new Error('fail')))).rejects.toThrow();
    await expect(cb.execute(() => Promise.reject(new Error('fail')))).rejects.toThrow();

    // 1 success resets
    await cb.execute(() => Promise.resolve('ok'));

    // 2 more failures should not trip the breaker
    await expect(cb.execute(() => Promise.reject(new Error('fail')))).rejects.toThrow();
    await expect(cb.execute(() => Promise.reject(new Error('fail')))).rejects.toThrow();

    expect(cb.getState()).toBe(CircuitState.CLOSED);
  });

  it('ignores failures outside the sliding time window', async () => {
    const cb = new CircuitBreaker({ failureThreshold: 3, windowMs: 10_000 });

    // 2 failures at t=0
    await expect(cb.execute(() => Promise.reject(new Error('fail')))).rejects.toThrow();
    await expect(cb.execute(() => Promise.reject(new Error('fail')))).rejects.toThrow();

    // Advance past window
    vi.advanceTimersByTime(11_000);

    // 1 more failure — old ones are pruned, only 1 in window
    await expect(cb.execute(() => Promise.reject(new Error('fail')))).rejects.toThrow();

    expect(cb.getState()).toBe(CircuitState.CLOSED);
  });

  it('uses default options when none provided', () => {
    const cb = new CircuitBreaker();
    // Just verify it constructs without error and is CLOSED
    expect(cb.getState()).toBe(CircuitState.CLOSED);
  });
});
