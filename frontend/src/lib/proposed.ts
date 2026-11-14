// ICP-02 — PROPOSED, NOT APPROVED. Types mirror
// contracts/openapi/gateway.extensions.proposed.v1.yaml. Until the team approves
// ICP-02 these are served ONLY by the mock API; there is deliberately no gateway
// implementation, so no unapproved route can ship (D2-FE-04 acceptance).

export interface Notification {
  notification_id: string;
  goal_id: string;
  goal_version: number;
  title: string;
  summary: string;
  created_at: string;
  is_read: boolean;
  /**
   * NOT in the ICP-02 proposal. Notification's own DB has `action_package_id nullable`
   * (docs/implementation/06-notification.md), but the public schema omits it, so the
   * feed can't link to its package. Requested as an ICP-02 amendment; mock-only until then.
   */
  action_package_id?: string | null;
}

export interface SellerOffer {
  seller_domain: string;
  source_url: string;
  observation_id: string;
  item_name: string;
  /** Decimal string in the proposal. */
  quantity: string;
  unit: "pcs" | "kg" | "L";
  total_item_eur_cents: number;
  /** Absent means unknown — never treat it as 0. */
  shipping_eur_cents?: number;
  additional_tax_eur_cents?: number;
}

export interface ActionPackage {
  package_id: string;
  goal_id: string;
  goal_version: number;
  evaluation_id: string;
  created_at: string;
  expires_at: string;
  approval_required: true;
  auto_execute: false;
  next_action: "request_user_approval";
  requires_checkout_revalidation: true;
  total_eur_cents?: number;
  seller_offers: SellerOffer[];
}

export interface ProposedApi {
  listNotifications(limit?: number): Promise<Notification[]>;
  markNotificationRead(notificationId: string): Promise<void>;
  /** 404 = absent or not yours; 410 = expired. */
  getActionPackage(packageId: string): Promise<ActionPackage>;
}
