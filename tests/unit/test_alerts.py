"""Tests for Prometheus alert rules validation."""

from __future__ import annotations

import yaml


class TestAlertRules:
    """Validate Prometheus alert rules configuration."""

    def setup_method(self) -> None:
        with open("prometheus/alerts.yml") as f:
            self.config = yaml.safe_load(f)

    def test_alert_rules_file_loads(self) -> None:
        assert self.config is not None
        assert "groups" in self.config

    def test_has_alert_group(self) -> None:
        groups = self.config["groups"]
        assert len(groups) >= 1
        assert groups[0]["name"] == "call_center_alerts"

    def test_all_required_alerts_exist(self) -> None:
        rules = self.config["groups"][0]["rules"]
        alert_names = {r["alert"] for r in rules}

        required_alerts = {
            "HighTransferRate",
            "PipelineErrorsHigh",
            "HighResponseLatency",
            "OperatorQueueOverflow",
            "AbnormalAPISpend",
            "SuspiciousToolCalls",
        }
        assert required_alerts.issubset(alert_names)

    def test_latency_alerts_exist(self) -> None:
        rules = self.config["groups"][0]["rules"]
        alert_names = {r["alert"] for r in rules}

        latency_alerts = {"HighSTTLatency", "HighLLMLatency", "HighTTSLatency"}
        assert latency_alerts.issubset(alert_names)

    def test_alerts_have_severity_labels(self) -> None:
        rules = self.config["groups"][0]["rules"]
        for rule in rules:
            assert "severity" in rule["labels"], f"Alert {rule['alert']} missing severity"

    def test_alerts_have_annotations(self) -> None:
        rules = self.config["groups"][0]["rules"]
        for rule in rules:
            assert "summary" in rule["annotations"], f"Alert {rule['alert']} missing summary"
            assert "description" in rule["annotations"], (
                f"Alert {rule['alert']} missing description"
            )

    def test_severity_values_valid(self) -> None:
        rules = self.config["groups"][0]["rules"]
        valid_severities = {"critical", "high", "medium", "low"}
        for rule in rules:
            severity = rule["labels"]["severity"]
            assert severity in valid_severities, (
                f"Alert {rule['alert']} has invalid severity: {severity}"
            )

    def test_critical_alerts(self) -> None:
        rules = self.config["groups"][0]["rules"]
        critical_alerts = [r for r in rules if r["labels"]["severity"] == "critical"]
        assert len(critical_alerts) >= 2  # At least OperatorQueueOverflow and SuspiciousToolCalls

    def test_transfer_rate_threshold(self) -> None:
        rules = self.config["groups"][0]["rules"]
        transfer_rule = next(r for r in rules if r["alert"] == "HighTransferRate")
        assert "0.5" in transfer_rule["expr"]  # 50% threshold

    def test_operator_queue_threshold(self) -> None:
        rules = self.config["groups"][0]["rules"]
        queue_rule = next(r for r in rules if r["alert"] == "OperatorQueueOverflow")
        assert "> 5" in queue_rule["expr"]


class TestAlertmanagerConfig:
    """Validate Alertmanager configuration."""

    def setup_method(self) -> None:
        with open("alertmanager/config.yml") as f:
            self.config = yaml.safe_load(f)

    def test_config_loads(self) -> None:
        assert self.config is not None

    def test_has_telegram_receiver(self) -> None:
        receivers = self.config["receivers"]
        receiver_names = [r["name"] for r in receivers]
        assert "telegram" in receiver_names

    def test_has_route_config(self) -> None:
        assert "route" in self.config
        assert "group_by" in self.config["route"]

    def test_critical_route_has_shorter_interval(self) -> None:
        routes = self.config["route"].get("routes", [])
        critical_route = next(
            (r for r in routes if r.get("match", {}).get("severity") == "critical"),
            None,
        )
        assert critical_route is not None
        assert "1h" in critical_route["repeat_interval"]


class TestPrometheusConfig:
    """Validate Prometheus configuration."""

    def setup_method(self) -> None:
        with open("prometheus/prometheus.yml") as f:
            self.config = yaml.safe_load(f)

    def test_config_loads(self) -> None:
        assert self.config is not None

    def test_has_scrape_config(self) -> None:
        assert "scrape_configs" in self.config
        jobs = [s["job_name"] for s in self.config["scrape_configs"]]
        assert "call-processor" in jobs

    def test_has_alertmanager_config(self) -> None:
        assert "alerting" in self.config

    def test_has_rule_files(self) -> None:
        assert "rule_files" in self.config
        assert "alerts.yml" in self.config["rule_files"]
