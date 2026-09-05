-- Meridian operations console - relational schema
-- Target server: MySQL 8.0, InnoDB, utf8mb4.
-- Running this file drops and recreates every object in the `meridian` database.

CREATE DATABASE IF NOT EXISTS `meridian`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE `meridian`;

SET NAMES utf8mb4;
SET SESSION sql_mode = 'STRICT_ALL_TABLES,NO_ENGINE_SUBSTITUTION';
SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS `import_batches`;
DROP TABLE IF EXISTS `saved_layouts`;
DROP TABLE IF EXISTS `export_jobs`;
DROP TABLE IF EXISTS `routing_rules`;
DROP TABLE IF EXISTS `integrations`;
DROP TABLE IF EXISTS `notification_log`;
DROP TABLE IF EXISTS `notification_templates`;
DROP TABLE IF EXISTS `notices`;
DROP TABLE IF EXISTS `audit_events`;
DROP TABLE IF EXISTS `approvals`;
DROP TABLE IF EXISTS `ledger_entries`;
DROP TABLE IF EXISTS `consignments`;
DROP TABLE IF EXISTS `invoices`;
DROP TABLE IF EXISTS `staff`;
DROP TABLE IF EXISTS `accounts`;

SET FOREIGN_KEY_CHECKS = 1;

-- ---------------------------------------------------------------------------
-- accounts: the customer companies Calderwood forwards freight for.
-- ---------------------------------------------------------------------------
CREATE TABLE `accounts` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `reference`     VARCHAR(16) NOT NULL,
  `name`          VARCHAR(120) NOT NULL,
  `legal_name`    VARCHAR(180) NOT NULL,
  `country_code`  CHAR(2) NOT NULL,
  `tier`          ENUM('standard','priority','strategic') NOT NULL DEFAULT 'standard',
  `status`        ENUM('active','suspended','closed') NOT NULL DEFAULT 'active',
  `onboarded_on`  DATE NOT NULL,
  `created_at`    DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_accounts_reference` (`reference`),
  KEY `ix_accounts_status` (`status`),
  KEY `ix_accounts_country` (`country_code`),
  KEY `ix_accounts_name` (`name`)
) ENGINE=InnoDB AUTO_INCREMENT=1054 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- staff: console users. `directory_uid` is the uid of the matching entry in the
-- corporate directory; the payroll columns are replicated here because the
-- reporting exports read from this table only.
-- ---------------------------------------------------------------------------
CREATE TABLE `staff` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `email`            VARCHAR(190) NOT NULL,
  `display_name`     VARCHAR(120) NOT NULL,
  `given_name`       VARCHAR(60) NOT NULL,
  `family_name`      VARCHAR(60) NOT NULL,
  `role`             ENUM('viewer','analyst','administrator') NOT NULL DEFAULT 'viewer',
  `account_id`       BIGINT UNSIGNED DEFAULT NULL,
  `directory_uid`    VARCHAR(64) DEFAULT NULL,
  `national_id`      VARCHAR(32) DEFAULT NULL,
  `pay_band`         VARCHAR(8) DEFAULT NULL,
  `recovery_secret`  VARCHAR(64) DEFAULT NULL,
  `password_hash`    VARCHAR(120) NOT NULL,
  `status`           ENUM('active','suspended','provisioning') NOT NULL DEFAULT 'provisioning',
  `last_seen_at`     DATETIME DEFAULT NULL,
  `created_at`       DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_staff_email` (`email`),
  UNIQUE KEY `uq_staff_directory_uid` (`directory_uid`),
  KEY `ix_staff_account` (`account_id`),
  KEY `ix_staff_role` (`role`),
  KEY `ix_staff_status` (`status`),
  CONSTRAINT `fk_staff_account` FOREIGN KEY (`account_id`)
    REFERENCES `accounts` (`id`) ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=4141 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- invoices: billing documents raised against an account.
-- ---------------------------------------------------------------------------
CREATE TABLE `invoices` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `account_id`  BIGINT UNSIGNED NOT NULL,
  `reference`   VARCHAR(24) NOT NULL,
  `issued_on`   DATE NOT NULL,
  `due_on`      DATE NOT NULL,
  `currency`    CHAR(3) NOT NULL DEFAULT 'EUR',
  `net_amount`  DECIMAL(12,2) NOT NULL,
  `tax_amount`  DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  `status`      ENUM('draft','issued','paid','overdue','void') NOT NULL DEFAULT 'draft',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_invoices_reference` (`reference`),
  KEY `ix_invoices_account` (`account_id`),
  KEY `ix_invoices_status` (`status`),
  KEY `ix_invoices_issued_on` (`issued_on`),
  CONSTRAINT `fk_invoices_account` FOREIGN KEY (`account_id`)
    REFERENCES `accounts` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=9000 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- consignments: booked freight movements.
-- ---------------------------------------------------------------------------
CREATE TABLE `consignments` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `account_id`       BIGINT UNSIGNED NOT NULL,
  `reference`        VARCHAR(24) NOT NULL,
  `origin_code`      VARCHAR(8) NOT NULL,
  `destination_code` VARCHAR(8) NOT NULL,
  `mode`             ENUM('road','sea','air','rail') NOT NULL,
  `weight_kg`        DECIMAL(10,2) NOT NULL,
  `volume_m3`        DECIMAL(10,3) NOT NULL,
  `status`           ENUM('booked','in_transit','held','cleared','delivered','cancelled') NOT NULL DEFAULT 'booked',
  `booked_at`        DATETIME NOT NULL,
  `cleared_at`       DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_consignments_reference` (`reference`),
  KEY `ix_consignments_account` (`account_id`),
  KEY `ix_consignments_status` (`status`),
  KEY `ix_consignments_booked_at` (`booked_at`),
  KEY `ix_consignments_lane` (`origin_code`, `destination_code`),
  CONSTRAINT `fk_consignments_account` FOREIGN KEY (`account_id`)
    REFERENCES `accounts` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=20000 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- ledger_entries: postings on the account ledger, optionally tied to a movement.
-- ---------------------------------------------------------------------------
CREATE TABLE `ledger_entries` (
  `id`              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `account_id`      BIGINT UNSIGNED NOT NULL,
  `entry_date`      DATE NOT NULL,
  `category`        VARCHAR(40) NOT NULL,
  `amount`          DECIMAL(12,2) NOT NULL,
  `currency`        CHAR(3) NOT NULL DEFAULT 'EUR',
  `memo`            VARCHAR(255) DEFAULT NULL,
  `consignment_id`  BIGINT UNSIGNED DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_ledger_account_date` (`account_id`, `entry_date`),
  KEY `ix_ledger_category` (`category`),
  KEY `ix_ledger_consignment` (`consignment_id`),
  CONSTRAINT `fk_ledger_account` FOREIGN KEY (`account_id`)
    REFERENCES `accounts` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT `fk_ledger_consignment` FOREIGN KEY (`consignment_id`)
    REFERENCES `consignments` (`id`) ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=60000 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- approvals: four-eyes requests raised by an analyst, decided by an administrator.
-- ---------------------------------------------------------------------------
CREATE TABLE `approvals` (
  `id`                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `account_id`         BIGINT UNSIGNED NOT NULL,
  `subject_type`       VARCHAR(40) NOT NULL,
  `subject_reference`  VARCHAR(40) NOT NULL,
  `requested_by`       BIGINT UNSIGNED NOT NULL,
  `requested_at`       DATETIME NOT NULL,
  `state`              ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending',
  `decided_by`         BIGINT UNSIGNED DEFAULT NULL,
  `decided_at`         DATETIME DEFAULT NULL,
  `note`               VARCHAR(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_approvals_account` (`account_id`),
  KEY `ix_approvals_state` (`state`),
  KEY `ix_approvals_subject` (`subject_type`, `subject_reference`),
  KEY `ix_approvals_requested_by` (`requested_by`),
  KEY `ix_approvals_decided_by` (`decided_by`),
  CONSTRAINT `fk_approvals_account` FOREIGN KEY (`account_id`)
    REFERENCES `accounts` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT `fk_approvals_requested_by` FOREIGN KEY (`requested_by`)
    REFERENCES `staff` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT `fk_approvals_decided_by` FOREIGN KEY (`decided_by`)
    REFERENCES `staff` (`id`) ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=7800 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- audit_events: append-only activity record kept for the retention window.
-- ---------------------------------------------------------------------------
CREATE TABLE `audit_events` (
  `id`                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `occurred_at`       DATETIME NOT NULL,
  `actor_id`          BIGINT UNSIGNED DEFAULT NULL,
  `action`            VARCHAR(60) NOT NULL,
  `object_type`       VARCHAR(40) NOT NULL,
  `object_reference`  VARCHAR(60) NOT NULL,
  `account_id`        BIGINT UNSIGNED DEFAULT NULL,
  `source_address`    VARCHAR(45) NOT NULL,
  `detail`            VARCHAR(500) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_audit_occurred_at` (`occurred_at`),
  KEY `ix_audit_actor` (`actor_id`),
  KEY `ix_audit_object` (`object_type`, `object_reference`),
  KEY `ix_audit_account` (`account_id`),
  CONSTRAINT `fk_audit_actor` FOREIGN KEY (`actor_id`)
    REFERENCES `staff` (`id`) ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT `fk_audit_account` FOREIGN KEY (`account_id`)
    REFERENCES `accounts` (`id`) ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=310000 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- notices: banner messages shown on the console home page.
-- ---------------------------------------------------------------------------
CREATE TABLE `notices` (
  `id`              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `title`           VARCHAR(160) NOT NULL,
  `body`            TEXT NOT NULL,
  `severity`        ENUM('info','warning','critical') NOT NULL DEFAULT 'info',
  `author_id`       BIGINT UNSIGNED NOT NULL,
  `published_from`  DATETIME NOT NULL,
  `published_to`    DATETIME DEFAULT NULL,
  `created_at`      DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_notices_window` (`published_from`, `published_to`),
  KEY `ix_notices_author` (`author_id`),
  CONSTRAINT `fk_notices_author` FOREIGN KEY (`author_id`)
    REFERENCES `staff` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=500 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- notification_templates: message bodies rendered with ${...} placeholders.
-- ---------------------------------------------------------------------------
CREATE TABLE `notification_templates` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `code`        VARCHAR(40) NOT NULL,
  `name`        VARCHAR(120) NOT NULL,
  `channel`     ENUM('email','sms','webhook') NOT NULL DEFAULT 'email',
  `body`        TEXT NOT NULL,
  `updated_by`  BIGINT UNSIGNED NOT NULL,
  `updated_at`  DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_templates_code` (`code`),
  KEY `ix_templates_channel` (`channel`),
  KEY `ix_templates_updated_by` (`updated_by`),
  CONSTRAINT `fk_templates_updated_by` FOREIGN KEY (`updated_by`)
    REFERENCES `staff` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=60 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- notification_log: one row per delivery attempt made by the dispatcher.
-- ---------------------------------------------------------------------------
CREATE TABLE `notification_log` (
  `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `template_code`  VARCHAR(40) NOT NULL,
  `account_id`     BIGINT UNSIGNED DEFAULT NULL,
  `channel`        ENUM('email','sms','webhook') NOT NULL,
  `recipient`      VARCHAR(190) NOT NULL,
  `sent_at`        DATETIME NOT NULL,
  `state`          ENUM('queued','sent','failed') NOT NULL DEFAULT 'queued',
  `detail`         VARCHAR(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_notification_log_code` (`template_code`),
  KEY `ix_notification_log_account` (`account_id`),
  KEY `ix_notification_log_sent_at` (`sent_at`),
  KEY `ix_notification_log_state` (`state`)
) ENGINE=InnoDB AUTO_INCREMENT=88000 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- integrations: outbound connections to carriers, brokers and customer systems.
-- ---------------------------------------------------------------------------
CREATE TABLE `integrations` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `account_id`       BIGINT UNSIGNED DEFAULT NULL,
  `kind`             ENUM('webhook','sftp','edi','carrier_api') NOT NULL,
  `name`             VARCHAR(120) NOT NULL,
  `endpoint_url`     VARCHAR(255) NOT NULL,
  `secret`           VARCHAR(120) NOT NULL,
  `status`           ENUM('active','paused','error') NOT NULL DEFAULT 'active',
  `last_refresh_at`  DATETIME DEFAULT NULL,
  `created_at`       DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_integrations_account` (`account_id`),
  KEY `ix_integrations_kind` (`kind`),
  KEY `ix_integrations_status` (`status`),
  CONSTRAINT `fk_integrations_account` FOREIGN KEY (`account_id`)
    REFERENCES `accounts` (`id`) ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=20 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- routing_rules: expressions evaluated by the dispatcher, highest priority first.
-- ---------------------------------------------------------------------------
CREATE TABLE `routing_rules` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name`        VARCHAR(120) NOT NULL,
  `expression`  VARCHAR(500) NOT NULL,
  `priority`    INT NOT NULL DEFAULT 100,
  `enabled`     TINYINT(1) NOT NULL DEFAULT 1,
  `updated_by`  BIGINT UNSIGNED NOT NULL,
  `updated_at`  DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_routing_rules_priority` (`priority`),
  KEY `ix_routing_rules_enabled` (`enabled`),
  KEY `ix_routing_rules_updated_by` (`updated_by`),
  CONSTRAINT `fk_routing_rules_updated_by` FOREIGN KEY (`updated_by`)
    REFERENCES `staff` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=40 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- export_jobs: background extracts requested from the reporting screens.
-- ---------------------------------------------------------------------------
CREATE TABLE `export_jobs` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `requested_by`  BIGINT UNSIGNED NOT NULL,
  `format`        ENUM('csv','xlsx','pdf') NOT NULL DEFAULT 'csv',
  `row_count`     INT NOT NULL DEFAULT 0,
  `state`         ENUM('queued','running','complete','failed') NOT NULL DEFAULT 'queued',
  `created_at`    DATETIME NOT NULL,
  `completed_at`  DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_export_jobs_requested_by` (`requested_by`),
  KEY `ix_export_jobs_state` (`state`),
  KEY `ix_export_jobs_created_at` (`created_at`),
  CONSTRAINT `fk_export_jobs_requested_by` FOREIGN KEY (`requested_by`)
    REFERENCES `staff` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=4400 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- saved_layouts: per-user grid layouts, serialised by the front end.
-- ---------------------------------------------------------------------------
CREATE TABLE `saved_layouts` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `staff_id`    BIGINT UNSIGNED NOT NULL,
  `name`        VARCHAR(120) NOT NULL,
  `state_blob`  MEDIUMBLOB NOT NULL,
  `updated_at`  DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_saved_layouts_staff` (`staff_id`),
  CONSTRAINT `fk_saved_layouts_staff` FOREIGN KEY (`staff_id`)
    REFERENCES `staff` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=2200 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- import_batches: uploaded booking and rate files awaiting or having had apply.
-- ---------------------------------------------------------------------------
CREATE TABLE `import_batches` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `requested_by`  BIGINT UNSIGNED NOT NULL,
  `filename`      VARCHAR(255) NOT NULL,
  `entry_count`   INT NOT NULL DEFAULT 0,
  `state`         ENUM('queued','extracted','applied','failed') NOT NULL DEFAULT 'queued',
  `created_at`    DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_import_batches_requested_by` (`requested_by`),
  KEY `ix_import_batches_state` (`state`),
  KEY `ix_import_batches_created_at` (`created_at`),
  CONSTRAINT `fk_import_batches_requested_by` FOREIGN KEY (`requested_by`)
    REFERENCES `staff` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=3300 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
