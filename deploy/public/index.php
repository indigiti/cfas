<?php
declare(strict_types=1);

// DigiOps public entrypoint for CFAS. Apache/PHP remains the public runtime;
// requests are forwarded to the private Flask worker bound to loopback only.
set_time_limit(150);

$backend = 'http://127.0.0.1:8765';
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
$url = $backend . $forwardPath . ($query !== '' ? '?' . $query : '');

$headers = [];
foreach (function_exists('getallheaders') ? getallheaders() : [] as $name => $value) {
    $lower = strtolower((string)$name);
    if (in_array($lower, ['host', 'content-length', 'connection', 'transfer-encoding'], true)) continue;
    $headers[] = $name . ': ' . $value;
}

$proto = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'https' : 'http';
$host = (string)($_SERVER['HTTP_HOST'] ?? 'localhost');
$remote = (string)($_SERVER['REMOTE_ADDR'] ?? '127.0.0.1');
$headers[] = 'Host: ' . $host;
$headers[] = 'X-Forwarded-Host: ' . $host;
$headers[] = 'X-Forwarded-Proto: ' . $proto;
$headers[] = 'X-Forwarded-Prefix: ' . $mount;
$headers[] = 'X-Forwarded-For: ' . $remote;

$ch = curl_init($url);
if ($ch === false) {
    http_response_code(503);
    header('Content-Type: application/json');
    echo json_encode(['ok' => false, 'message' => 'CFAS runtime is unavailable.']);
    exit;
}

$method = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));
$options = [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HEADER => true,
    CURLOPT_FOLLOWLOCATION => false,
    CURLOPT_CONNECTTIMEOUT => 3,
    CURLOPT_TIMEOUT => 135,
    CURLOPT_CUSTOMREQUEST => $method,
    CURLOPT_HTTPHEADER => $headers,
];
if (!in_array($method, ['GET', 'HEAD'], true)) {
    $options[CURLOPT_POSTFIELDS] = file_get_contents('php://input') ?: '';
}
curl_setopt_array($ch, $options);
$response = curl_exec($ch);
if ($response === false) {
    $error = curl_error($ch);
    curl_close($ch);
    http_response_code(503);
    header('Content-Type: application/json');
    header('Retry-After: 5');
    echo json_encode(['ok' => false, 'message' => 'CFAS runtime is starting or unavailable.', 'detail' => $error]);
    exit;
}

$status = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
$headerSize = (int)curl_getinfo($ch, CURLINFO_HEADER_SIZE);
curl_close($ch);

$rawHeaders = substr($response, 0, $headerSize);
$body = substr($response, $headerSize);
http_response_code($status > 0 ? $status : 502);

foreach (preg_split('/\r\n|\r|\n/', trim($rawHeaders)) ?: [] as $line) {
    if ($line === '' || str_starts_with($line, 'HTTP/')) continue;
    $pos = strpos($line, ':');
    if ($pos === false) continue;
    $name = trim(substr($line, 0, $pos));
    $value = trim(substr($line, $pos + 1));
    $lower = strtolower($name);
    if (in_array($lower, ['content-length', 'transfer-encoding', 'connection', 'server'], true)) continue;
    if ($lower === 'location' && str_starts_with($value, '/') && !str_starts_with($value, $mount . '/')) {
        $value = $mount . $value;
    }
    header($name . ': ' . $value, false);
}

echo $body;
