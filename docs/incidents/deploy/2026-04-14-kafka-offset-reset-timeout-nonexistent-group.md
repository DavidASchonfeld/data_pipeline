# Kafka Offset Reset Prints Java Timeout Error for Non-Existent Consumer Groups

**Date:** 2026-04-14
**Severity:** Low (alarming error printed to terminal; deploy completed and reported no warnings)
**Affected component:** `scripts/deploy/airflow_pods.sh` — Phase D (Kafka consumer group offset reset)

---

## What was the problem

After running `./scripts/deploy.sh --provision --snowflake-setup`, a wall of Java error text appeared in the terminal during the Kafka offset reset step:

```
=== Resetting Kafka consumer group offsets to latest ===

Error: Executing consumer group command failed due to java.util.concurrent.ExecutionException: org.apache.kafka.common.errors.TimeoutException: Timed out waiting for a node assignment. Call: describeConsumerGroups(api=DESCRIBE_GROUPS)
java.lang.RuntimeException: java.util.concurrent.ExecutionException: ...
    at org.apache.kafka.tools.consumer.group.ConsumerGroupCommand$ConsumerGroupService ...
    ...
Caused by: org.apache.kafka.common.errors.TimeoutException: Timed out waiting for a node assignment.

GROUP                          TOPIC                          PARTITION  NEW-OFFSET
weather-consumer-group         weather-hourly-raw             0          0
Kafka consumer group offsets reset to latest.
```

Despite the error, the deploy finished with "DEPLOY COMPLETE" and no warnings.

Here is what happened, step by step:

1. The deploy ran on a fresh or recently replaced spot instance. The Kafka broker had just been started for the first time and the topics had just been created.

2. Phase D of the post-deploy setup tries to reset the offset position for two consumer groups (`stocks-consumer-group` and `weather-consumer-group`). An offset is essentially a bookmark that tells a consumer where it left off reading in a topic.

3. When the reset command ran for `stocks-consumer-group`, Kafka tried to look up which broker was responsible for managing that group. Every consumer group has a designated broker (called a "coordinator") that tracks where the group is up to. But `stocks-consumer-group` had never connected before — no stocks DAG had ever run on this instance — so the group did not exist anywhere in Kafka's records. With no group record, there was no coordinator to find, and the lookup timed out with the Java error above.

4. Despite printing the error, Kafka's command-line tool returned a success exit code (0). This is Kafka's own inconsistency: it considers "there was nothing to reset" a success, even though it printed an error. Because the exit code was 0, the `&&` chain in the script continued and ran the weather group reset, which succeeded (the weather group existed from a previous deploy on the same instance).

5. The final "Kafka consumer group offsets reset to latest." message was printed, which made it look like everything worked — but `stocks-consumer-group` was never actually reset. This does not matter on a fresh deploy (a group that has never connected has no stale offset to clear), but the Java stack trace is alarming and suggests something broke.

---

## What was changed

**`scripts/deploy/airflow_pods.sh`**

Added a group-existence check before the reset. The script now lists all current consumer groups first, then only calls `--reset-offsets` for groups that actually appear in that list. If a group is not found, it prints a plain message and moves on.

```bash
# Before: called --reset-offsets unconditionally for each group
kubectl exec kafka-0 -n kafka -- \
    /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server localhost:9092 \
    --group stocks-consumer-group \
    --reset-offsets --to-latest \
    --topic stocks-financials-raw --execute &&
kubectl exec kafka-0 -n kafka -- \
    ...

# After: lists existing groups first, skips reset for any group that doesn't exist yet
EXISTING=$(kubectl exec kafka-0 -n kafka -- \
    /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server localhost:9092 --list 2>/dev/null || echo '')
for pair in stocks-consumer-group:stocks-financials-raw weather-consumer-group:weather-hourly-raw; do
    group=${pair%%:*}
    topic=${pair##*:}
    if echo "$EXISTING" | grep -q "^$group$"; then
        kubectl exec kafka-0 -n kafka -- \
            /opt/kafka/bin/kafka-consumer-groups.sh \
            --bootstrap-server localhost:9092 \
            --group "$group" --reset-offsets --to-latest \
            --topic "$topic" --execute
    else
        echo "$group not found — skipping reset (fresh deploy, consumer has not connected yet)"
    fi
done
```

On a fresh deploy, both groups are skipped with a clear one-line message instead of a Java stack trace. On a redeploy where both groups exist, both are reset exactly as before.

---

## Why this didn't happen before

The offset reset step was originally added to fix a specific problem: after a pod restart or fresh deploy, a consumer group's previously committed position is lost. Without a committed position, the consumer starts reading from the very end of the topic, which means it misses any messages the producer published just before it started. Resetting the offset to the current end of the topic before the producer runs ensures the consumer picks up the very next message.

That fix assumed the consumer groups already existed (it was written for the redeploy case, where groups had been created by earlier DAG runs). On a completely fresh spot instance — one that has never run the stocks or weather DAGs — the groups do not exist yet. The reset step had no check for this and ran the command anyway, producing the timeout error.

The error was harmless in practice: a non-existent group has no stale offset, so there is nothing to reset. Skipping it on a fresh deploy is the correct behavior.

---

## Files changed

| File | Change |
|------|--------|
| `scripts/deploy/airflow_pods.sh` | Phase D now lists existing consumer groups first and skips reset for any group that does not exist yet, replacing the Java TimeoutException with a plain informational message |
