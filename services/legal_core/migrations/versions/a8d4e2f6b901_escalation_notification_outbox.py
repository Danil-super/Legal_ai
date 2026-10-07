"""Durable, tenant-bound lawyer alerts queued atomically with each escalation."""

from alembic import op

revision = "a8d4e2f6b901"
down_revision = "a7c9e1f2b3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE public.escalation_notifications (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            clinic_id uuid NOT NULL,
            escalation_id uuid NOT NULL,
            recipient_membership_id uuid NOT NULL,
            state varchar(20) NOT NULL DEFAULT 'PENDING'
                CHECK (state IN ('PENDING','LEASED','DELIVERED','CANCELLED','UNDELIVERABLE')),
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            lease_token uuid,
            lease_until timestamptz,
            next_attempt_at timestamptz NOT NULL DEFAULT now(),
            delivered_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(escalation_id,recipient_membership_id),
            FOREIGN KEY(clinic_id,escalation_id)
                REFERENCES public.case_escalations(clinic_id,id) ON DELETE CASCADE,
            FOREIGN KEY(clinic_id,recipient_membership_id)
                REFERENCES public.clinic_users(clinic_id,id) ON DELETE RESTRICT,
            CHECK ((state='LEASED') = (lease_token IS NOT NULL AND lease_until IS NOT NULL))
        );
        CREATE INDEX ix_escalation_notifications_pending
            ON public.escalation_notifications(next_attempt_at,id)
            WHERE state IN ('PENDING','LEASED');
        ALTER TABLE public.escalation_notifications ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.escalation_notifications FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_escalation_notifications ON public.escalation_notifications
            USING(clinic_id=nullif(current_setting('app.current_clinic_id',true),'')::uuid)
            WITH CHECK(clinic_id=nullif(current_setting('app.current_clinic_id',true),'')::uuid);

        CREATE FUNCTION public.enqueue_escalation_notifications() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
        BEGIN
            INSERT INTO public.escalation_notifications(
                clinic_id,escalation_id,recipient_membership_id)
            SELECT NEW.clinic_id,NEW.id,cu.id FROM public.clinic_users cu
            JOIN public.users u ON u.id=cu.user_id
            WHERE cu.clinic_id=NEW.clinic_id AND cu.status='ACTIVE'
                AND cu.role='CLINIC_LAWYER' AND u.status='ACTIVE'
            ON CONFLICT(escalation_id,recipient_membership_id) DO NOTHING;
            RETURN NEW;
        END; $$;
        REVOKE ALL ON FUNCTION public.enqueue_escalation_notifications() FROM PUBLIC;
        CREATE TRIGGER escalation_notifications_enqueue AFTER INSERT
            ON public.case_escalations FOR EACH ROW
            EXECUTE FUNCTION public.enqueue_escalation_notifications();
        INSERT INTO public.escalation_notifications(clinic_id,escalation_id,recipient_membership_id)
            SELECT e.clinic_id,e.id,cu.id FROM public.case_escalations e
            JOIN public.cases c ON c.id=e.case_id AND c.clinic_id=e.clinic_id
            JOIN public.clinic_users cu ON cu.clinic_id=e.clinic_id
            JOIN public.users u ON u.id=cu.user_id
            WHERE cu.status='ACTIVE' AND cu.role='CLINIC_LAWYER' AND u.status='ACTIVE'
                AND c.retention_due_at>now() AND c.content_purged_at IS NULL
                AND NOT EXISTS(SELECT 1 FROM public.case_escalation_workflow_events w
                    WHERE w.escalation_id=e.id AND w.clinic_id=e.clinic_id)
            ON CONFLICT(escalation_id,recipient_membership_id) DO NOTHING;

        CREATE FUNCTION public.escalation_notification_is_eligible(notification_id uuid)
        RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public AS $$
            SELECT EXISTS(
                SELECT 1 FROM public.escalation_notifications n
                JOIN public.case_escalations e ON e.id=n.escalation_id AND e.clinic_id=n.clinic_id
                JOIN public.cases c ON c.id=e.case_id AND c.clinic_id=n.clinic_id
                JOIN public.clinics clinic ON clinic.id=n.clinic_id
                JOIN public.clinic_users cu ON cu.id=n.recipient_membership_id
                    AND cu.clinic_id=n.clinic_id
                JOIN public.users u ON u.id=cu.user_id
                WHERE n.id=notification_id AND e.level IN ('HIGH','CRITICAL')
                    AND clinic.status='ACTIVE' AND cu.status='ACTIVE'
                    AND cu.role='CLINIC_LAWYER' AND u.status='ACTIVE'
                    AND c.retention_due_at>now() AND c.content_purged_at IS NULL
                    AND NOT EXISTS(SELECT 1 FROM public.case_escalation_workflow_events w
                        WHERE w.clinic_id=n.clinic_id AND w.escalation_id=n.escalation_id)
                    AND (SELECT count(*) FROM public.clinic_users m WHERE m.user_id=u.id
                        AND m.status='ACTIVE'
                        AND m.role IN ('CLINIC_OWNER','CLINIC_ADMIN','CLINIC_LAWYER'))=1
                    AND EXISTS(SELECT 1 FROM public.subscription_entitlements se
                        WHERE se.clinic_id=n.clinic_id AND se.user_id=u.id AND se.status='ACTIVE'
                        AND se.starts_at<=now() AND (se.ends_at IS NULL OR se.ends_at>now()))
            );
        $$;
        REVOKE ALL ON FUNCTION public.escalation_notification_is_eligible(uuid) FROM PUBLIC;

        CREATE FUNCTION public.claim_escalation_notifications()
        RETURNS TABLE(notification_id uuid,lease_token uuid,case_id uuid,escalation_id uuid,
                      risk_level varchar,telegram_user_id bigint)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
        BEGIN
            UPDATE public.escalation_notifications n SET state='CANCELLED',
                lease_token=NULL,lease_until=NULL,updated_at=now()
                WHERE n.id IN (SELECT stale.id FROM public.escalation_notifications stale
                    WHERE stale.state IN ('PENDING','LEASED')
                        AND NOT public.escalation_notification_is_eligible(stale.id)
                    ORDER BY stale.created_at,stale.id
                    FOR UPDATE SKIP LOCKED LIMIT 100);
            RETURN QUERY
            WITH candidates AS (
                SELECT n.id FROM public.escalation_notifications n
                JOIN public.case_escalations e ON e.id=n.escalation_id AND e.clinic_id=n.clinic_id
                WHERE (n.state='PENDING' OR (n.state='LEASED' AND n.lease_until<=now()))
                    AND n.next_attempt_at<=now()
                    AND public.escalation_notification_is_eligible(n.id)
                ORDER BY n.next_attempt_at,n.id FOR UPDATE OF e,n SKIP LOCKED LIMIT 20
            ), claimed AS (
                UPDATE public.escalation_notifications n SET state='LEASED',
                    attempts=n.attempts+1,lease_token=gen_random_uuid(),
                    lease_until=now()+interval '60 seconds',updated_at=now()
                FROM candidates c WHERE n.id=c.id RETURNING n.*
            ) SELECT n.id,n.lease_token,e.case_id,e.id,e.level,u.telegram_user_id
                FROM claimed n JOIN public.case_escalations e ON e.id=n.escalation_id
                JOIN public.clinic_users cu ON cu.id=n.recipient_membership_id
                JOIN public.users u ON u.id=cu.user_id;
        END; $$;
        REVOKE ALL ON FUNCTION public.claim_escalation_notifications() FROM PUBLIC;

        CREATE FUNCTION public.check_escalation_notification(notification_id uuid,token uuid)
        RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public AS $$
        DECLARE eligible boolean;
        BEGIN
            PERFORM 1 FROM public.escalation_notifications n JOIN public.case_escalations e
                ON e.id=n.escalation_id AND e.clinic_id=n.clinic_id
                WHERE n.id=notification_id AND n.state='LEASED' AND n.lease_token=token
                    AND n.lease_until>now() FOR UPDATE OF e,n;
            IF NOT FOUND THEN RETURN false; END IF;
            eligible := public.escalation_notification_is_eligible(notification_id);
            IF NOT eligible THEN
                UPDATE public.escalation_notifications n SET state='CANCELLED',
                    lease_token=NULL,lease_until=NULL,updated_at=now() WHERE n.id=notification_id;
            END IF;
            RETURN eligible;
        END; $$;
        REVOKE ALL ON FUNCTION public.check_escalation_notification(uuid,uuid) FROM PUBLIC;

        CREATE FUNCTION public.complete_escalation_notification(
            notification_id uuid,token uuid,outcome varchar,retry_after_seconds integer)
        RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public AS $$
        DECLARE target public.escalation_notifications;
        BEGIN
            IF outcome NOT IN ('DELIVERED','RETRY','UNDELIVERABLE')
                OR retry_after_seconds<0 OR retry_after_seconds>86400 THEN
                RAISE EXCEPTION 'invalid notification result';
            END IF;
            SELECT * INTO target FROM public.escalation_notifications n
                WHERE n.id=notification_id AND n.state='LEASED' AND n.lease_token=token
                    AND n.lease_until>now() FOR UPDATE;
            IF NOT FOUND THEN RETURN false; END IF;
            UPDATE public.escalation_notifications n SET
                state=CASE WHEN outcome='RETRY' THEN 'PENDING' ELSE outcome END,
                lease_token=NULL,lease_until=NULL,updated_at=now(),
                delivered_at=CASE WHEN outcome='DELIVERED' THEN now() ELSE NULL END,
                next_attempt_at=now()+make_interval(secs=>greatest(retry_after_seconds,
                    CASE WHEN outcome='RETRY' THEN least(3600,5*power(2,
                        least(target.attempts-1,10)))::integer ELSE 0 END))
                WHERE n.id=notification_id;
            INSERT INTO public.audit_events(clinic_id,actor_membership_id,action,resource_type,
                                            resource_id,metadata_json,correlation_id)
                VALUES(target.clinic_id,target.recipient_membership_id,
                    'ESCALATION_NOTIFICATION_'||outcome,'CASE_ESCALATION',target.escalation_id,
                    jsonb_build_object('notificationId',target.id,'attempt',target.attempts),
                    gen_random_uuid());
            RETURN true;
        END; $$;
        REVOKE ALL ON FUNCTION public.complete_escalation_notification(uuid,uuid,varchar,integer)
            FROM PUBLIC;
    """)


def downgrade() -> None:
    # Operator explicitly chooses to stop delivery; already sent Telegram messages remain.
    op.execute("DROP TRIGGER escalation_notifications_enqueue ON public.case_escalations")
    op.execute("DROP FUNCTION public.enqueue_escalation_notifications()")
    op.execute("DROP FUNCTION public.complete_escalation_notification(uuid,uuid,varchar,integer)")
    op.execute("DROP FUNCTION public.check_escalation_notification(uuid,uuid)")
    op.execute("DROP FUNCTION public.claim_escalation_notifications()")
    op.execute("DROP FUNCTION public.escalation_notification_is_eligible(uuid)")
    op.drop_table("escalation_notifications")
