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
 */
function hcaptcha_verify(string $response, string $remote_ip): bool {
    if (!hcaptcha_enabled()) return true;
    if ($response === '') return false;

    $post = http_build_query([
        'secret'   => hcaptcha_secret(),
        'response' => $response,
        'remoteip' => $remote_ip,
    ]);
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
        error_log('hcaptcha_verify: siteverify request failed');
        return false;
    }
    $data = json_decode($body, true);
    return is_array($data) && !empty($data['success']);
}
