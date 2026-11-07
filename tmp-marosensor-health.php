<?php
/**
 * Точка проверки живости сайта для внешнего вотчдога.
 * Отвечает 200 и «ok», только если WordPress и база реально работают.
 */
declare(strict_types=1);

require __DIR__ . '/wp-load.php';

header('Content-Type: text/plain; charset=utf-8');
header('Cache-Control: no-store, max-age=0');
header('X-Robots-Tag: noindex, nofollow');

echo 'ok ' . get_bloginfo('name') . ' ' . current_time('mysql');
