<?php
/**
 * Every request that is not a file on disk arrives here.
 */

declare(strict_types=1);

require dirname(__DIR__) . '/app/bootstrap.php';

bt_dispatch();
