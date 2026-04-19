<?php
declare(strict_types=1);
/**
 * hCaptcha verification helper.
 *
 * Enabled only when HCAPTCHA_SITE_KEY and HCAPTCHA_SECRET are both set.
 * When disabled, hcaptcha_verify() returns true — useful for dev and for
 * Phase 1 rollout before keys are obtained.
 */

function _hcaptcha_env(string $name): string {
    $v = getenv($name);
    if ($v === false || $v === '') $v = (string)($_SERVER[$name] ?? '');
    return $v;
}

function hcaptcha_site_key(): string { return _hcaptcha_env('HCAPTCHA_SITE_KEY'); }
function hcaptcha_secret(): string { return _hcaptcha_env('HCAPTCHA_SECRET'); }

function hcaptcha_enabled(): bool {
    return hcaptcha_site_key() !== '' && hcaptcha_secret() !== '';
}

/** Emit the <script> tag for the hCaptcha client library. */
function hcaptcha_script_tag(): string {
    if (!hcaptcha_enabled()) return '';
    return '<script src="https://js.hcaptcha.com/1/api.js" async defer></script>';
}

/** Emit the captcha widget div for a form. */
function hcaptcha_widget(): string {
    if (!hcaptcha_enabled()) return '';
    $key = htmlspecialchars(hcaptcha_site_key());
    return '<div class="h-captcha" data-sitekey="' . $key . '"></div>';
}

/**
 * Verify an hCaptcha response against the siteverify endpoint.
 *
 * Returns true if disabled or verification succeeds, false otherwise.
 * On every failure path, logs a detail line via error_log() so the
 * operator can see the upstream error-codes in the PHP-FPM log.
 */
function hcaptcha_verify(string $response, string $remote_ip): bool {
    if (!hcaptcha_enabled()) return true;
    if ($response === '') {
        error_log('hcaptcha_verify: empty h-captcha-response from client');
        return false;
    }

    $post = http_build_query([
        'secret'   => hcaptcha_secret(),
        'response' => $response,
        'remoteip' => $remote_ip,
    ]);

    // Prefer curl when available — better TLS + timeout semantics than
    // file_get_contents, and surfaces network errors clearly.
    if (function_exists('curl_init')) {
        $ch = curl_init('https://api.hcaptcha.com/siteverify');
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
            error_log("hcaptcha_verify: curl failed http=$code err=$err");
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
        $body = @file_get_contents('https://api.hcaptcha.com/siteverify', false, $ctx);
        if ($body === false) {
            error_log('hcaptcha_verify: file_get_contents failed (check allow_url_fopen + TLS)');
            return false;
        }
    }

    $data = json_decode($body, true);
    if (!is_array($data)) {
        error_log('hcaptcha_verify: non-JSON response: ' . substr((string)$body, 0, 300));
        return false;
    }
    if (!empty($data['success'])) return true;

    $codes = $data['error-codes'] ?? [];
    error_log('hcaptcha_verify: success=false error-codes='
              . (is_array($codes) ? implode(',', $codes) : (string)$codes)
              . ' response-host=' . ($data['hostname'] ?? '(none)'));
    return false;
}
