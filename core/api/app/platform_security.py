def secure_tenant_ids(cur, principal):
    cur.execute("SELECT condominium_id FROM user_condominiums WHERE user_id=%s", (principal["user_id"],))
    allowed = [r["condominium_id"] for r in cur.fetchall()]

    if allowed:
        tenant_csv = ",".join(str(x) for x in allowed)
        cur.execute("SET LOCAL ROLE vision_portal")
        cur.execute("SELECT set_config('app.tenant_ids', %s, true)", (tenant_csv,))
    return allowed
