<?php
declare(strict_types=1);
/**
 * HTTP termination seam.
 *
 * Production code ends an early-out (404/400/403) with
 * `http_terminate($code, $body)` instead of a bare `http_response_code()+exit`.
 * In normal operation it sets the status, emits the body, and `exit`s exactly
 * as before. Under tests (the `KAYAK_TEST` constant, defined by the PHPUnit
 * bootstrap) it throws `HttpExitException` instead — so an in-process
 * functional test can call a handler, catch the exception, and assert on the
 * status code without the bare `exit` killing the whole test run.
 */

/** Thrown by http_terminate() under tests in place of exit(). */
final class HttpExitException extends \RuntimeException
{
    public function __construct(public readonly int $statusCode, string $body = '')
    {
        parent::__construct($body, $statusCode);
    }
}

/**
 * End the request with an HTTP status + optional plain body. Throws under tests
 * (see KAYAK_TEST), exits otherwise.
 */
function http_terminate(int $code, string $body = ''): never
{
    // constant() (not a bare KAYAK_TEST reference) so PHPStan doesn't flag the
    // test-only constant as undefined in the production scan.
    if (defined('KAYAK_TEST') && constant('KAYAK_TEST') === true) {
        throw new HttpExitException($code, $body);
    }
    http_response_code($code);
    if ($body !== '') {
        echo $body;
    }
    exit;
}
