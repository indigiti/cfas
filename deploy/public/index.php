<?php
declare(strict_types=1);

// CFAS runs under Apache/PHP on Cloudways. Dynamic requests are handed to the
// private Flask WSGI application through a short-lived Python CGI process.
// This avoids requiring a persistent Gunicorn/systemd/supervisor process.
set_time_limit(150);

$mount = '/cfas';
$requestUri = (string)($_SERVER['REQUEST_URI'] ?? '/');
$path = (string)(parse_url($requestUri, PHP_URL_PATH) ?? '/');
$query = (string)(parse_url($requestUri, PHP_URL_QUERY) ?? '');

if ($path === $mount) {
    $forwardPath = '/';
} elseif (str_starts_with($path, $mount . '/')) {
    $forwardPath = substr($path, strlen($mount));
} else {
    $forwardPath = $path;
}
if ($forwardPath === '') $forwardPath = '/';

$appHome = dirname(dirname(__DIR__));
$privateRoot = $appHome . '/private_html/cfas';
$gateway = $privateRoot . '/scripts/cgi-gateway.py';
$python = '/usr/bin/python3';

function cfas_fail(string $message, string $detail = '', int $status = 503): never {
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode([
        'ok' => false,
        'message' => $message,
        'detail' => $detail,
    ], JSON_UNESCAPED_SLASHES);
    exit;
}

if (!is_file($gateway)) {
    cfas_fail('CFAS private runtime is incomplete.', 'Python gateway is missing.');
}
if (!is_executable($python)) {
    cfas_fail('CFAS Python runtime is unavailable.', 'Expected /usr/bin/python3.');
}
if (!function_exists('proc_open')) {
    cfas_fail('CFAS runtime cannot start requests.', 'PHP proc_open is unavailable.');
}

$body = file_get_contents('php://input');
if ($body === false) $body = '';

$env = getenv();
if (!is_array($env)) $env = [];
$env['REQUEST_METHOD'] = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));
$env['QUERY_STRING'] = $query;
$env['CONTENT_TYPE'] = (string)($_SERVER['CONTENT_TYPE'] ?? $_SERVER['HTTP_CONTENT_TYPE'] ?? '');
$env['CONTENT_LENGTH'] = (string)strlen($body);
$env['SCRIPT_NAME'] = $mount;
$env['PATH_INFO'] = $forwardPath;
$env['REQUEST_URI'] = $requestUri;
$env['SERVER_NAME'] = (string)($_SERVER['SERVER_NAME'] ?? 'localhost');
$env['SERVER_PORT'] = (string)($_SERVER['SERVER_PORT'] ?? '443');
$env['SERVER_PROTOCOL'] = (string)($_SERVER['SERVER_PROTOCOL'] ?? 'HTTP/1.1');
$env['SERVER_SOFTWARE'] = 'CFAS-PHP-Bridge';
$env['GATEWAY_INTERFACE'] = 'CGI/1.1';
$env['REMOTE_ADDR'] = (string)($_SERVER['REMOTE_ADDR'] ?? '127.0.0.1');
$env['HTTPS'] = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'on' : 'off';
$env['HOME'] = $privateRoot . '/.runtime-home';
$env['TMPDIR'] = $privateRoot . '/run/tmp';
$env['PYTHONUNBUFFERED'] = '1';

foreach (function_exists('getallheaders') ? getallheaders() : [] as $name => $value) {
    $upper = strtoupper(str_replace('-', '_', (string)$name));
    if ($upper === 'CONTENT_TYPE' || $upper === 'CONTENT_LENGTH') continue;
    $env['HTTP_' . $upper] = (string)$value;
}

$host = (string)($_SERVER['HTTP_HOST'] ?? $_SERVER['SERVER_NAME'] ?? 'localhost');
$proto = $env['HTTPS'] === 'on' ? 'https' : 'http';
$env['HTTP_HOST'] = $host;
$env['HTTP_X_FORWARDED_HOST'] = $host;
$env['HTTP_X_FORWARDED_PROTO'] = $proto;
$env['HTTP_X_FORWARDED_PREFIX'] = $mount;
$env['HTTP_X_FORWARDED_FOR'] = (string)($_SERVER['REMOTE_ADDR'] ?? '127.0.0.1');

$descriptors = [
    0 => ['pipe', 'r'],
    1 => ['pipe', 'w'],
    2 => ['pipe', 'w'],
];
$process = @proc_open([$python, $gateway], $descriptors, $pipes, $privateRoot, $env);
if (!is_resource($process)) {
    cfas_fail('CFAS Python request could not be started.');
}

$offset = 0;
$length = strlen($body);
while ($offset < $length) {
    $written = fwrite($pipes[0], substr($body, $offset));
    if ($written === false || $written === 0) break;
    $offset += $written;
}
fclose($pipes[0]);

$stdout = stream_get_contents($pipes[1]);
$stderr = stream_get_contents($pipes[2]);
fclose($pipes[1]);
fclose($pipes[2]);
$exitCode = proc_close($process);

if (!is_string($stdout) || $stdout === '') {
    cfas_fail(
        'CFAS Python request failed.',
        trim((string)$stderr) !== '' ? substr(trim((string)$stderr), 0, 1200) : 'No response from Python gateway.',
        502
    );
}

$separator = strpos($stdout, "\r\n\r\n");
$separatorLength = 4;
if ($separator === false) {
    $separator = strpos($stdout, "\n\n");
    $separatorLength = 2;
}
if ($separator === false) {
    cfas_fail('CFAS returned an invalid gateway response.', substr(trim((string)$stderr), 0, 1200), 502);
}

$rawHeaders = substr($stdout, 0, $separator);
$responseBody = substr($stdout, $separator + $separatorLength);
$status = 200;

foreach (preg_split('/\r\n|\r|\n/', trim($rawHeaders)) ?: [] as $line) {
    if ($line === '') continue;
    $pos = strpos($line, ':');
    if ($pos === false) continue;
    $name = trim(substr($line, 0, $pos));
    $value = trim(substr($line, $pos + 1));
    $lower = strtolower($name);

    if ($lower === 'status') {
        if (preg_match('/^(\d{3})\b/', $value, $match)) $status = (int)$match[1];
        continue;
    }
    if (in_array($lower, ['content-length', 'transfer-encoding', 'connection', 'server'], true)) continue;
    if ($lower === 'location' && str_starts_with($value, '/') && !str_starts_with($value, $mount . '/')) {
        $value = $mount . $value;
    }
    header($name . ': ' . $value, false);
}

http_response_code($status);
echo $responseBody;
