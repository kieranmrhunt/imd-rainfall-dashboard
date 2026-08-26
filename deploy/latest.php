<?php
declare(strict_types=1);

header('Access-Control-Allow-Origin: *');
header('Cache-Control: no-store, max-age=0');
header('Content-Type: application/json; charset=utf-8');

$path = __DIR__ . '/latest.json';
if (!is_readable($path)) {
    http_response_code(503);
    echo json_encode(['error' => 'Recent IMD rainfall data are not available yet']);
    exit;
}

$modified = filemtime($path);
if ($modified !== false) {
    header('Last-Modified: ' . gmdate('D, d M Y H:i:s', $modified) . ' GMT');
}
readfile($path);
