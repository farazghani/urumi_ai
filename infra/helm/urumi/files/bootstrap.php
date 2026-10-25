<?php
// Stable description makes retries and upgrades update the same API key.
if (!class_exists('WC_Install')) {
    WP_CLI::error('WooCommerce is unavailable.');
}
if (get_option('woocommerce_db_version') !== WC()->version) {
    WC_Install::install();
}
$user = get_user_by('login', getenv('WORDPRESS_ADMIN_USER'));
if (!$user || !user_can($user, 'manage_woocommerce')) {
    WP_CLI::error('The configured WordPress admin must exist and manage WooCommerce.');
}
$key = getenv('WOO_CONSUMER_KEY');
$secret = getenv('WOO_CONSUMER_SECRET');
if (!preg_match('/^ck_[a-f0-9]{40}$/', $key) || !preg_match('/^cs_[a-f0-9]{40}$/', $secret)) {
    WP_CLI::error('Invalid WooCommerce API key format in Secret.');
}
global $wpdb;
// A failed credential write must not print the SQL (and its secret) to Job logs.
$wpdb->suppress_errors(true);
$table = $wpdb->prefix . 'woocommerce_api_keys';
$description = 'urumi-infra';
$id = $wpdb->get_var($wpdb->prepare("SELECT key_id FROM $table WHERE description = %s", $description));
$record = ['user_id' => $user->ID, 'description' => $description, 'permissions' => 'read_write',
           'consumer_key' => wc_api_hash($key), 'consumer_secret' => $secret,
           'truncated_key' => substr($key, -7)];
$result = $id ? $wpdb->update($table, $record, ['key_id' => $id]) : $wpdb->insert($table, $record);
if ($result === false) {
    WP_CLI::error('Could not configure the WooCommerce API key.');
}
// Pretty permalinks are required for /wp-json/wc/v3 routes.
update_option('permalink_structure', '/%postname%/');
update_option('woocommerce_onboarding_profile', ['completed' => true]);
flush_rewrite_rules(true);
