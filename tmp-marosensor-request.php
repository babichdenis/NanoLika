<?php
/**
 * Plugin Name: Marosensor request form
 * Description: Приём заявок с формы на странице «Обращение и контакты»: письмо на почту
 *              клиента с полями заявки, ответ посетителю — возврат на страницу с отметкой.
 * Version: 1.0
 */

if (!defined('ABSPATH')) {
    exit;
}

/** Куда уходят заявки. Меняется одной строкой. */
if (!defined('MAROSENSOR_REQUEST_TO')) {
    define('MAROSENSOR_REQUEST_TO', 'office@memsnav.ru');
}

/** Страница с формой: куда возвращаем посетителя. */
if (!defined('MAROSENSOR_REQUEST_PAGE')) {
    define('MAROSENSOR_REQUEST_PAGE', '/kontakty/');
}

function marosensor_request_fields(): array
{
    return [
        'topic' => 'Тема обращения',
        'model' => 'Прибор',
        'company' => 'Компания',
        'person' => 'Имя',
        'position' => 'Должность',
        'email' => 'E-mail',
        'phone' => 'Телефон',
        'object' => 'Объект установки',
        'message' => 'Сообщение',
    ];
}

function marosensor_request_back(string $status): void
{
    wp_safe_redirect(add_query_arg('sent', $status, home_url(MAROSENSOR_REQUEST_PAGE)) . '#request');
    exit;
}

function marosensor_request_handle(): void
{
    $nonce = isset($_POST['_wpnonce']) ? sanitize_text_field(wp_unslash($_POST['_wpnonce'])) : '';
    if (!wp_verify_nonce($nonce, 'marosensor_request')) {
        marosensor_request_back('bad');
    }
    // ловушка для ботов: поле, которого человек не видит
    if (!empty($_POST['website'])) {
        marosensor_request_back('ok');
    }

    $values = [];
    foreach (marosensor_request_fields() as $key => $label) {
        $raw = isset($_POST[$key]) ? wp_unslash($_POST[$key]) : '';
        $values[$key] = sanitize_textarea_field(is_string($raw) ? $raw : '');
    }
    $email = sanitize_email($values['email']);
    if ('' === trim($values['person']) || !is_email($email) || '' === trim($values['message'])) {
        marosensor_request_back('invalid');
    }

    $lines = [];
    foreach (marosensor_request_fields() as $key => $label) {
        if ('' !== trim($values[$key])) {
            $lines[] = $label . ': ' . $values[$key];
        }
    }
    $lines[] = '';
    $lines[] = 'Отправлено с ' . home_url(MAROSENSOR_REQUEST_PAGE);
    $lines[] = 'IP: ' . ($_SERVER['REMOTE_ADDR'] ?? '');
    $body = implode("\n", $lines);

    $subject = 'Заявка с сайта marosensor.ru — ' . ($values['topic'] ?: 'обращение');
    $headers = [
        'Content-Type: text/plain; charset=UTF-8',
        'Reply-To: ' . $values['person'] . ' <' . $email . '>',
    ];

    $sent = wp_mail(MAROSENSOR_REQUEST_TO, $subject, $body, $headers);

    // Журнал отправок: если письмо не ушло, видно почему (в веб не отдаётся).
    $log = WP_CONTENT_DIR . '/uploads/marosensor-requests.log';
    @file_put_contents(
        $log,
        date('c') . ' ' . ($sent ? 'SENT' : 'FAIL') . ' to=' . MAROSENSOR_REQUEST_TO . ' email=' . $email . "\n",
        FILE_APPEND
    );

    marosensor_request_back($sent ? 'ok' : 'fail');
}

add_action('admin_post_nopriv_marosensor_request', 'marosensor_request_handle');
add_action('admin_post_marosensor_request', 'marosensor_request_handle');


/**
 * Форма в контенте страницы написана как mailto: — заменяем её на реальную отправку.
 * Действие и nonce подставляются на рендере, поэтому живут всегда свежими.
 */
add_filter('the_content', function (string $content): string {
    if (strpos($content, 'mailto:office@memsnav.ru') === false) {
        return $content;
    }
    $hidden = wp_nonce_field('marosensor_request', '_wpnonce', true, false)
        . '<input type="hidden" name="action" value="marosensor_request">'
        . '<input type="text" name="website" value="" tabindex="-1" autocomplete="off" aria-hidden="true" style="position:absolute;left:-9999px;width:1px;height:1px;opacity:0">';
    $content = str_replace(
        'action="mailto:office@memsnav.ru" method="post" enctype="text/plain"',
        'action="' . esc_url(admin_url('admin-post.php')) . '" method="post"',
        $content
    );
    $content = preg_replace('/(<form[^>]*class="request-form"[^>]*>)/', '\1' . $hidden, $content, 1);
    return $content;
}, 19);

/** Отметка для посетителя над формой. */
add_filter('the_content', function (string $content): string {
    if (!is_page() || !isset($_GET['sent'])) {
        return $content;
    }
    $status = sanitize_key(wp_unslash($_GET['sent']));
    $messages = [
        'ok' => ['Спасибо, заявка отправлена.', 'Мы ответим на указанный e-mail или позвоним.'],
        'invalid' => ['Проверьте поля формы.', 'Нужны имя, корректный e-mail и сообщение.'],
        'bad' => ['Форма устарела.', 'Обновите страницу и отправьте ещё раз.'],
        'fail' => ['Не удалось отправить заявку.', 'Напишите нам напрямую: ' . MAROSENSOR_REQUEST_TO],
    ];
    if (!isset($messages[$status])) {
        return $content;
    }
    [$title, $note] = $messages[$status];
    $class = 'ok' === $status ? 'request-note request-note-ok' : 'request-note request-note-bad';
    $html = '<div class="' . esc_attr($class) . '" role="status"><strong>'
        . esc_html($title) . '</strong> ' . esc_html($note) . '</div>';
    return $html . $content;
}, 20);
