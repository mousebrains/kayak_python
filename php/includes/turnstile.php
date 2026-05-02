<?php
declare(strict_types=1);
/**
 * Cloudflare Turnstile verification helper.
 *
 * Enabled only when TURNSTILE_SITE_KEY and TURNSTILE_SECRET are both set.
 * When disabled, turnstile_verify() returns true — useful for dev and for
 * pre-rollout before keys are obtained.
 */

function _turnstile_env(string $name): string {
    $v = getenv($name);
    if ($v === false || $v === '') $v = (string)($_SERVER[$name] ?? '');
    return $v;
}

function turnstile_site_key(): string { return _turnstile_env('TURNSTILE_SITE_KEY'); }
function turnstile_secret(): string { return _turnstile_env('TURNSTILE_SECRET'); }

function turnstile_enabled(): bool {
    return turnstile_site_key() !== '' && turnstile_secret() !== '';
}

/** Emit the <script> tag for the Turnstile client library. */
function turnstile_script_tag(): string {
    if (!turnstile_enabled()) return '';
    return '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>';
}

/** Emit the captcha widget div for a form. */
function turnstile_widget(): string {
    if (!turnstile_enabled()) return '';
    $key = htmlspecialchars(turnstile_site_key());
    return '<div class="cf-turnstile" data-sitekey="' . $key . '"></div>';
}

/**
 * Verify a Turnstile response against the siteverify endpoint.
 *
 * Returns true if disabled or verification succeeds, false otherwise.
 * On every failure path, logs a detail line via error_log() so the
 * operator can see the upstream error-codes in the PHP-FPM log.
 */
function turnstile_verify(string $response, string $remote_ip): bool {
    if (!turnstile_enabled()) return true;
    if ($response === '') {
        error_log('turnstile_verify: empty cf-turnstile-response from client');
        return false;
    }

    $post = http_build_query([
        'secret'   => turnstile_secret(),
        'response' => $response,
        'remoteip' => $remote_ip,
    ]);

    $url = 'https://challenges.cloudflare.com/turnstile/v0/siteverify';

    if (function_exists('curl_init')) {
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_POST           => true,
            CURLOPT_POSTFIELDS     => $post,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT        => 5,
            CURLOPT_CONNECTTIMEOUT => 3,
            CURLOPT_HTTPHEADER     => ['Content-Type: application/x-www-form-urlencoded'],
        ]);
        $body = curl_exec($ch);
        $err  = curl_error($ch);
        $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
        if ($body === false || $body === '') {
            error_log("turnstile_verify: curl failed http=$code err=$err");
            return false;
        }
    } else {
        $ctx = stream_context_create([
            'http' => [
                'method'  => 'POST',
                'header'  => "Content-Type: application/x-www-form-urlencoded\r\n",
                'content' => $post,
                'timeout' => 5,
                'ignore_errors' => true,
            ],
        ]);
        $body = @file_get_contents($url, false, $ctx);
        if ($body === false) {
            error_log('turnstile_verify: file_get_contents failed (check allow_url_fopen + TLS)');
            return false;
        }
    }

    $data = json_decode($body, true);
    if (!is_array($data)) {
        error_log('turnstile_verify: non-JSON response: ' . substr((string)$body, 0, 300));
        return false;
    }
    if (!empty($data['success'])) return true;

    $codes = $data['error-codes'] ?? [];
    error_log('turnstile_verify: success=false error-codes='
              . (is_array($codes) ? implode(',', $codes) : (string)$codes)
              . ' hostname=' . ($data['hostname'] ?? '(none)'));
    return false;
}
