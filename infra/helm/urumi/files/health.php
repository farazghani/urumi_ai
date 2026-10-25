<?php
// Deliberately works before core installation so the post-install Job can run.
// Do not load WordPress here: database outages must not affect liveness.
header('Content-Type: text/plain');
if (($_GET['check'] ?? '') === 'ready') {
    mysqli_report(MYSQLI_REPORT_OFF);
    $parts = explode(':', getenv('WORDPRESS_DB_HOST'), 2);
    $db = @new mysqli($parts[0], getenv('WORDPRESS_DB_USER'), getenv('WORDPRESS_DB_PASSWORD'),
                     getenv('WORDPRESS_DB_NAME'), intval($parts[1] ?? 3306));
    if ($db->connect_errno || !$db->query('SELECT 1')) {
        http_response_code(503);
        exit('unavailable');
    }
    $db->close();
}
echo "ok\n";
