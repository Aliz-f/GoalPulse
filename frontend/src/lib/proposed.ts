// Prepared purchase (action package) — MOCK ONLY. This is the older ICP-02 proposal
// shape (`seller_offers`). The accepted Phase 2 contract groups items by seller
// (`seller_orders`, P1, R2-D4-03), and the public Notification has no link to a package,
// so gateway mode never shows a package. Notifications themselves moved to the
// accepted contract in extension.ts (R2-D2-04).

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
  /** 404 = absent or not yours; 410 = expired. */
  getActionPackage(packageId: string): Promise<ActionPackage>;
}
