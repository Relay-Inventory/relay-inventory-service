from __future__ import annotations

import argparse
from typing import Optional

import boto3


def _alarm_name(prefix: str, name: str, tenant_id: Optional[str]) -> str:
    if tenant_id:
        return f"{prefix}-{tenant_id}-{name}"
    return f"{prefix}-{name}"


def _dimensions(tenant_id: Optional[str]) -> list[dict]:
    if not tenant_id:
        return []
    return [{"Name": "tenant_id", "Value": tenant_id}]


def _alarm_actions(topic_arn: Optional[str]) -> list[str]:
    if not topic_arn:
        return []
    return [topic_arn]


def main() -> None:
    parser = argparse.ArgumentParser(description="Create CloudWatch alarms for Relay Inventory")
    parser.add_argument("--alarm-prefix", default="relay-inventory", help="Alarm name prefix")
    parser.add_argument(
        "--namespace",
        default="RelayInventory",
        help="CloudWatch namespace for custom metrics",
    )
    parser.add_argument("--sns-topic-arn", help="SNS topic ARN for alarm actions")
    parser.add_argument("--tenant-id", help="Optional tenant ID for per-tenant alarms")
    parser.add_argument(
        "--consecutive-failure-threshold",
        type=int,
        default=3,
        help="Datapoints/evaluation periods for consecutive failures",
    )
    parser.add_argument(
        "--consecutive-failure-period",
        type=int,
        default=300,
        help="Period in seconds for consecutive failure alarm",
    )
    parser.add_argument(
        "--queue-depth-threshold",
        type=int,
        default=100,
        help="Queue depth threshold for backlog alarm",
    )
    parser.add_argument(
        "--queue-depth-period",
        type=int,
        default=300,
        help="Period in seconds for queue depth alarm",
    )
    parser.add_argument(
        "--queue-depth-evaluation-periods",
        type=int,
        default=3,
        help="Evaluation periods for queue depth alarm",
    )
    parser.add_argument(
        "--sqs-queue-name",
        help="SQS queue name for backlog alarm (required for queue depth alarm)",
    )
    parser.add_argument(
        "--worker-error-threshold",
        type=int,
        default=5,
        help="Worker error count threshold",
    )
    parser.add_argument(
        "--worker-error-period",
        type=int,
        default=300,
        help="Period in seconds for worker error alarm",
    )
    parser.add_argument(
        "--worker-error-evaluation-periods",
        type=int,
        default=1,
        help="Evaluation periods for worker error alarm",
    )

    args = parser.parse_args()

    cloudwatch = boto3.client("cloudwatch")
    alarm_actions = _alarm_actions(args.sns_topic_arn)

    cloudwatch.put_metric_alarm(
        AlarmName=_alarm_name(args.alarm_prefix, "consecutive-failures", args.tenant_id),
        AlarmDescription=(
            "Triggers on consecutive failed runs. "
            "Metric is written as 1 for failure and 0 for success."
        ),
        Namespace=args.namespace,
        MetricName="RunFailed",
        Dimensions=_dimensions(args.tenant_id),
        Statistic="Maximum",
        Period=args.consecutive_failure_period,
        EvaluationPeriods=args.consecutive_failure_threshold,
        DatapointsToAlarm=args.consecutive_failure_threshold,
        Threshold=1,
        ComparisonOperator="GreaterThanOrEqualToThreshold",
        TreatMissingData="notBreaching",
        AlarmActions=alarm_actions,
        OKActions=alarm_actions,
    )

    if args.sqs_queue_name:
        cloudwatch.put_metric_alarm(
            AlarmName=_alarm_name(args.alarm_prefix, "queue-backlog", args.tenant_id),
            AlarmDescription="Triggers on sustained SQS backlog.",
            Namespace="AWS/SQS",
            MetricName="ApproximateNumberOfMessagesVisible",
            Dimensions=[{"Name": "QueueName", "Value": args.sqs_queue_name}],
            Statistic="Average",
            Period=args.queue_depth_period,
            EvaluationPeriods=args.queue_depth_evaluation_periods,
            DatapointsToAlarm=args.queue_depth_evaluation_periods,
            Threshold=args.queue_depth_threshold,
            ComparisonOperator="GreaterThanOrEqualToThreshold",
            TreatMissingData="notBreaching",
            AlarmActions=alarm_actions,
            OKActions=alarm_actions,
        )

    cloudwatch.put_metric_alarm(
        AlarmName=_alarm_name(args.alarm_prefix, "worker-error-rate", args.tenant_id),
        AlarmDescription="Triggers on elevated worker error rate.",
        Namespace=args.namespace,
        MetricName="WorkerError",
        Dimensions=[],
        Statistic="Sum",
        Period=args.worker_error_period,
        EvaluationPeriods=args.worker_error_evaluation_periods,
        DatapointsToAlarm=args.worker_error_evaluation_periods,
        Threshold=args.worker_error_threshold,
        ComparisonOperator="GreaterThanOrEqualToThreshold",
        TreatMissingData="notBreaching",
        AlarmActions=alarm_actions,
        OKActions=alarm_actions,
    )


if __name__ == "__main__":
    main()
