<?php
/**
 * Plugin Name: Marosensor form wiring
 * Description: Форма на странице «Обращение и контакты» была написана как mailto: —
 *              она ничего не отправляла. Здесь её действие переводится на обработчик
 *              заявок (mu-plugin ms-leads: таблица + письмо), плюс метка времени и
 *              ловушка для ботов, которых этот обработчик ждёт.
 * Version: 1.1
 */

if (!defined('ABSPATH')) {
    exit;
}

add_filter('the_content', function (string $content): string {
    if (strpos($content, 'mailto:office@memsnav.ru') === false) {
        return $content;
    }
    $hidden = '<input type="hidden" name="action" value="ms_lead">'
        . '<input type="hidden" name="ms_ts" value="' . esc_attr((string) time()) . '">'
        . '<input type="text" name="ms_hp" value="" tabindex="-1" autocomplete="off" aria-hidden="true"'
        . ' style="position:absolute;left:-9999px;width:1px;height:1px;opacity:0">';

    $content = str_replace(
        'action="mailto:office@memsnav.ru" method="post" enctype="text/plain"',
        'action="' . esc_url(admin_url('admin-post.php')) . '" method="post"',
        $content
    );

    return preg_replace_callback(
        '/(<form[^>]*class="request-form"[^>]*>)/',
        static fn (array $m): string => $m[1] . $hidden,
        $content,
        1
    );
}, 19);
