<?php
/**
 * Plugin Name: Marosensor real client IP behind our failover proxy
 * Description: Когда сайт идёт через наш прокси (151.248.123.86), возвращает
 *              WordPress настоящий IP посетителя: логи, ограничение входов и
 *              антиспам должны видеть клиента, а не прокси.
 * Version: 1.0
 */

if (!defined('ABSPATH')) {
    exit;
}

const MAROSENSOR_PROXY_IP = '151.248.123.86';

add_action('init', function (): void {
    if (($_SERVER['REMOTE_ADDR'] ?? '') !== MAROSENSOR_PROXY_IP) {
        return;
    }
    $forwarded = $_SERVER['HTTP_X_FORWARDED_FOR'] ?? '';
    if ('' === $forwarded) {
        return;
    }
    $ip = trim(explode(',', $forwarded)[0]);
    if (filter_var($ip, FILTER_VALIDATE_IP)) {
        $_SERVER['REMOTE_ADDR'] = $ip;
    }
}, 0);
