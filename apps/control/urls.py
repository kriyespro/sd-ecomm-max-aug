from django.urls import path

from . import billing_views as billv
from . import catalog_views as cat
from . import onboarding_views as onbv
from . import cms_views as cmsv
from . import coupon_views as coup
from . import customer_views as custv
from . import domain_views as domv
from . import inventory_views as invv
from . import order_views as ordv
from . import payment_views as payv
from . import phase11_views as p11
from . import plan_views as planv
from . import review_views as revv
from . import seo_views as seov
from . import shipping_views as shipv
from . import skin_views as skinv
from . import store_views as storev
from . import team_views as teamv
from . import views

app_name = "control"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("stats/", views.StatsCardsView.as_view(), name="stats_cards"),
    path("activity/", views.ActivityFeedView.as_view(), name="activity_feed"),

    # Store switcher
    path("choose-store/", views.ProjectPickerView.as_view(), name="project_picker"),
    path("set-store/", views.SetProjectView.as_view(), name="set_project"),

    # First-run setup wizard (store owner / manager)
    path("start/", onbv.OnboardingView.as_view(), name="onboarding"),
    path("start/skip/", onbv.OnboardingSkipView.as_view(), name="onboarding_skip"),

    # Team (per-store staff)
    path("team/", teamv.TeamListView.as_view(), name="team"),
    path("team/add/", teamv.TeamAddView.as_view(), name="team_add"),
    path("team/<int:pk>/role/", teamv.TeamRoleView.as_view(), name="team_role"),
    path("team/<int:pk>/remove/", teamv.TeamRemoveView.as_view(), name="team_remove"),

    # Storefront skins
    path("skins/", skinv.SkinListView.as_view(), name="skin_list"),
    path("skins/new/", skinv.SkinCreateView.as_view(), name="skin_create"),
    path("skins/upload/", skinv.SkinUploadView.as_view(), name="skin_upload"),
    path("skins/store-access/", skinv.StoreSkinAccessView.as_view(), name="store_skin_access"),
    path("skins/<int:pk>/", skinv.SkinUpdateView.as_view(), name="skin_edit"),
    path("skins/<int:pk>/review/", skinv.SkinReviewView.as_view(), name="skin_review"),
    path("skins/<int:pk>/promote/", skinv.SkinPromoteView.as_view(), name="skin_promote"),
    path("skins/<int:pk>/toggle/", skinv.SkinToggleActiveView.as_view(), name="skin_toggle"),
    path("skins/<int:pk>/default/", skinv.SkinSetDefaultView.as_view(), name="skin_default"),

    # Users (platform-wide directory)
    path("users/", views.UserListView.as_view(), name="users"),
    path("users/new/", views.UserCreateView.as_view(), name="user_create"),
    path("partners/", views.PartnerApplicationListView.as_view(), name="partner_applications"),
    path("partners/<int:pk>/review/", views.PartnerApplicationReviewView.as_view(), name="partner_application_review"),
    path("users/<int:pk>/", views.UserDetailView.as_view(), name="user_detail"),
    path("users/<int:pk>/set-password/", views.UserSetPasswordView.as_view(), name="user_set_password"),
    path("users/<int:pk>/ban/", views.UserBanView.as_view(), name="user_ban"),
    path("users/<int:pk>/unban/", views.UserUnbanView.as_view(), name="user_unban"),
    path("users/<int:pk>/impersonate/", views.ImpersonateView.as_view(), name="user_impersonate"),
    path("impersonate/active/", views.ImpersonateActiveView.as_view(), name="impersonate_active"),
    path("impersonate/stop/", views.StopImpersonateView.as_view(), name="impersonate_stop"),

    # Categories
    path("categories/", cat.CategoryListView.as_view(), name="category_list"),
    path("categories/new/", cat.CategoryCreateView.as_view(), name="category_create"),
    path("categories/<int:pk>/", cat.CategoryUpdateView.as_view(), name="category_edit"),
    path("categories/<int:pk>/delete/", cat.CategoryDeleteView.as_view(), name="category_delete"),

    # Product types
    path("product-types/", cat.ProductTypeListView.as_view(), name="product_type_list"),
    path("product-types/new/", cat.ProductTypeCreateView.as_view(), name="product_type_create"),
    path("product-types/<int:pk>/", cat.ProductTypeUpdateView.as_view(), name="product_type_edit"),
    path("product-types/<int:pk>/delete/", cat.ProductTypeDeleteView.as_view(), name="product_type_delete"),

    # Tags
    path("tags/", cat.TagListView.as_view(), name="tag_list"),
    path("tags/new/", cat.TagCreateView.as_view(), name="tag_create"),
    path("tags/<int:pk>/", cat.TagUpdateView.as_view(), name="tag_edit"),
    path("tags/<int:pk>/delete/", cat.TagDeleteView.as_view(), name="tag_delete"),

    # Brands
    path("brands/", cat.BrandListView.as_view(), name="brand_list"),
    path("brands/new/", cat.BrandCreateView.as_view(), name="brand_create"),
    path("brands/<int:pk>/", cat.BrandUpdateView.as_view(), name="brand_edit"),
    path("brands/<int:pk>/delete/", cat.BrandDeleteView.as_view(), name="brand_delete"),

    # Products
    path("products/", cat.ProductListView.as_view(), name="product_list"),
    path("products/new/", cat.ProductCreateView.as_view(), name="product_create"),
    path("products/<int:pk>/", cat.ProductUpdateView.as_view(), name="product_edit"),
    path("products/<int:pk>/delete/", cat.ProductDeleteView.as_view(), name="product_delete"),
    path("products/<int:pk>/duplicate/", cat.ProductDuplicateView.as_view(), name="product_duplicate"),
    path("products/<int:pk>/images/", cat.ProductImagePanelView.as_view(), name="product_images"),
    path("products/<int:pk>/images/upload/", cat.ProductImageUploadView.as_view(), name="product_image_upload"),
    path("products/<int:pk>/images/<int:image_pk>/primary/", cat.ProductImagePrimaryView.as_view(), name="product_image_primary"),
    path("products/<int:pk>/images/<int:image_pk>/delete/", cat.ProductImageDeleteView.as_view(), name="product_image_delete"),
    path("products/<int:pk>/images/<int:image_pk>/move/<str:dir>/", cat.ProductImageMoveView.as_view(), name="product_image_move"),

    # Warehouses
    path("warehouses/", invv.WarehouseListView.as_view(), name="warehouse_list"),
    path("warehouses/new/", invv.WarehouseCreateView.as_view(), name="warehouse_create"),
    path("warehouses/<int:pk>/", invv.WarehouseUpdateView.as_view(), name="warehouse_edit"),
    path("warehouses/<int:pk>/delete/", invv.WarehouseDeleteView.as_view(), name="warehouse_delete"),

    # Inventory
    path("inventory/", invv.InventoryListView.as_view(), name="inventory_list"),
    path("inventory/new/", invv.InventoryItemCreateView.as_view(), name="inventory_create"),
    path("inventory/<int:pk>/adjust/", invv.InventoryAdjustView.as_view(), name="inventory_adjust"),
    path("inventory/<int:pk>/movements/", invv.ItemMovementsView.as_view(), name="inventory_movements"),

    # Orders
    path("orders/", ordv.OrderListView.as_view(), name="order_list"),
    path("orders/<int:pk>/", ordv.OrderDetailView.as_view(), name="order_detail"),
    path("orders/<int:pk>/status/", ordv.OrderStatusView.as_view(), name="order_status"),
    path("orders/<int:pk>/payment/", ordv.OrderPaymentView.as_view(), name="order_payment"),
    path("orders/<int:pk>/fulfill/", ordv.OrderFulfillView.as_view(), name="order_fulfill"),
    path("orders/<int:pk>/note/", ordv.OrderNoteView.as_view(), name="order_note"),
    path("orders/<int:pk>/shipping/", ordv.OrderShippingView.as_view(), name="order_shipping"),
    path("orders/<int:pk>/pay/initiate/", payv.OrderInitiatePaymentView.as_view(), name="order_pay_initiate"),
    path("orders/<int:pk>/pay/offline/", payv.OrderOfflinePaymentView.as_view(), name="order_pay_offline"),
    path("orders/<int:pk>/pay/<int:payment_pk>/capture/", payv.OrderCapturePaymentView.as_view(), name="order_pay_capture"),
    path("orders/<int:pk>/pay/<int:payment_pk>/refund/", payv.OrderRefundView.as_view(), name="order_pay_refund"),

    # Payments — provider configuration + reconciliation
    path("payments/providers/", payv.ProviderConfigListView.as_view(), name="payment_providers"),
    path("payments/providers/new/", payv.ProviderConfigCreateView.as_view(), name="payment_provider_create"),
    path("payments/providers/<int:pk>/", payv.ProviderConfigUpdateView.as_view(), name="payment_provider_edit"),
    path("payments/reconcile/", payv.ReconcileView.as_view(), name="payment_reconcile"),

    # Shipping — zones + methods
    path("shipping/zones/", shipv.ZoneListView.as_view(), name="shipping_zones"),
    path("shipping/zones/new/", shipv.ZoneCreateView.as_view(), name="shipping_zone_create"),
    path("shipping/zones/<int:pk>/", shipv.ZoneUpdateView.as_view(), name="shipping_zone_edit"),
    path("shipping/zones/<int:pk>/delete/", shipv.ZoneDeleteView.as_view(), name="shipping_zone_delete"),
    path("shipping/methods/new/", shipv.MethodCreateView.as_view(), name="shipping_method_create"),
    path("shipping/methods/<int:pk>/", shipv.MethodUpdateView.as_view(), name="shipping_method_edit"),
    path("shipping/methods/<int:pk>/delete/", shipv.MethodDeleteView.as_view(), name="shipping_method_delete"),

    # Shipping — per order
    path("orders/<int:pk>/shipping/set/", shipv.OrderSetShippingView.as_view(), name="order_shipping_set"),
    path("orders/<int:pk>/shipping/ship/", shipv.OrderCreateShipmentView.as_view(), name="order_shipment_create"),
    path("orders/<int:pk>/shipping/<int:shipment_pk>/status/", shipv.ShipmentStatusView.as_view(), name="order_shipment_status"),

    # Customers
    path("customers/", custv.CustomerListView.as_view(), name="customers"),
    path("customers/<int:pk>/", custv.CustomerDetailView.as_view(), name="customer_detail"),
    path("customers/<int:pk>/edit/", custv.CustomerUpdateView.as_view(), name="customer_edit"),
    path("customers/<int:pk>/block/", custv.CustomerBlockView.as_view(), name="customer_block"),
    path("customers/<int:pk>/unblock/", custv.CustomerUnblockView.as_view(), name="customer_unblock"),
    path("customers/<int:pk>/resync/", custv.CustomerResyncView.as_view(), name="customer_resync"),
    path("customer-groups/", custv.GroupListView.as_view(), name="customer_groups"),
    path("customer-groups/new/", custv.GroupCreateView.as_view(), name="customer_group_create"),
    path("customer-groups/<int:pk>/", custv.GroupUpdateView.as_view(), name="customer_group_edit"),
    path("customer-groups/<int:pk>/delete/", custv.GroupDeleteView.as_view(), name="customer_group_delete"),

    # Coupons
    path("coupons/", coup.CouponListView.as_view(), name="coupon_list"),
    path("coupons/new/", coup.CouponCreateView.as_view(), name="coupon_create"),
    path("coupons/<int:pk>/", coup.CouponUpdateView.as_view(), name="coupon_edit"),
    path("coupons/<int:pk>/delete/", coup.CouponDeleteView.as_view(), name="coupon_delete"),
    path("coupons/<int:pk>/redemptions/", coup.CouponRedemptionsView.as_view(), name="coupon_redemptions"),

    # Reviews
    path("reviews/", revv.ReviewListView.as_view(), name="review_list"),
    path("reviews/<int:pk>/moderate/", revv.ReviewModerateView.as_view(), name="review_moderate"),

    # Analytics + reports
    path("analytics/", p11.AnalyticsView.as_view(), name="analytics"),
    path("reports/", p11.ReportsView.as_view(), name="reports"),
    path("reports/export/", p11.ReportExportView.as_view(), name="report_export"),

    # Webhooks
    path("webhooks/", p11.WebhookListView.as_view(), name="webhooks"),
    path("webhooks/new/", p11.WebhookCreateView.as_view(), name="webhook_create"),
    path("webhooks/<int:pk>/", p11.WebhookUpdateView.as_view(), name="webhook_edit"),
    path("webhooks/<int:pk>/delete/", p11.WebhookDeleteView.as_view(), name="webhook_delete"),
    path("webhooks/deliveries/", p11.WebhookDeliveriesView.as_view(), name="webhook_deliveries"),
    path("webhooks/deliveries/<int:pk>/retry/", p11.WebhookRetryView.as_view(), name="webhook_retry"),

    # Notifications
    path("notifications/", p11.NotificationSettingsView.as_view(), name="notification_settings"),
    path("notifications/templates/new/", p11.NotifTemplateCreateView.as_view(), name="notification_template_create"),
    path("notifications/templates/<int:pk>/", p11.NotifTemplateUpdateView.as_view(), name="notification_template_edit"),
    path("notifications/templates/<int:pk>/delete/", p11.NotifTemplateDeleteView.as_view(), name="notification_template_delete"),

    # Media library
    path("media/", p11.MediaLibraryView.as_view(), name="media"),
    path("media/upload/", p11.MediaUploadView.as_view(), name="media_upload"),
    path("media/<int:pk>/delete/", p11.MediaDeleteView.as_view(), name="media_delete"),

    # CMS — pages
    path("cms/pages/", cmsv.PageListView.as_view(), name="cms_pages"),
    path("cms/pages/new/", cmsv.PageCreateView.as_view(), name="cms_page_create"),
    path("cms/pages/<int:pk>/", cmsv.PageUpdateView.as_view(), name="cms_page_edit"),
    path("cms/pages/<int:pk>/delete/", cmsv.PageDeleteView.as_view(), name="cms_page_delete"),
    # CMS — banners
    path("cms/banners/", cmsv.BannerListView.as_view(), name="cms_banners"),
    path("cms/banners/new/", cmsv.BannerCreateView.as_view(), name="cms_banner_create"),
    path("cms/banners/<int:pk>/", cmsv.BannerUpdateView.as_view(), name="cms_banner_edit"),
    path("cms/banners/<int:pk>/delete/", cmsv.BannerDeleteView.as_view(), name="cms_banner_delete"),
    # CMS — FAQs
    path("cms/faqs/", cmsv.FAQListView.as_view(), name="cms_faqs"),
    path("cms/faqs/new/", cmsv.FAQCreateView.as_view(), name="cms_faq_create"),
    path("cms/faqs/<int:pk>/", cmsv.FAQUpdateView.as_view(), name="cms_faq_edit"),
    path("cms/faqs/<int:pk>/delete/", cmsv.FAQDeleteView.as_view(), name="cms_faq_delete"),
    # CMS — content blocks
    path("cms/blocks/", cmsv.ContentBlockListView.as_view(), name="cms_blocks"),
    path("cms/blocks/new/", cmsv.ContentBlockCreateView.as_view(), name="cms_block_create"),
    path("cms/blocks/<int:pk>/", cmsv.ContentBlockUpdateView.as_view(), name="cms_block_edit"),
    path("cms/blocks/<int:pk>/delete/", cmsv.ContentBlockDeleteView.as_view(), name="cms_block_delete"),
    # CMS — menus
    path("cms/menus/", cmsv.MenuListView.as_view(), name="cms_menus"),
    path("cms/menus/new/", cmsv.MenuCreateView.as_view(), name="cms_menu_create"),
    path("cms/menus/<int:pk>/edit/", cmsv.MenuUpdateView.as_view(), name="cms_menu_edit"),
    path("cms/menus/<int:pk>/delete/", cmsv.MenuDeleteView.as_view(), name="cms_menu_delete"),
    path("cms/menus/<int:pk>/", cmsv.MenuDetailView.as_view(), name="cms_menu_detail"),
    path("cms/menus/<int:pk>/items/add/", cmsv.MenuItemCreateView.as_view(), name="cms_menu_item_add"),
    path("cms/menus/<int:pk>/items/<int:item_pk>/edit/", cmsv.MenuItemUpdateView.as_view(), name="cms_menu_item_edit"),
    path("cms/menus/<int:pk>/items/<int:item_pk>/delete/", cmsv.MenuItemDeleteView.as_view(), name="cms_menu_item_delete"),
    # CMS — theme
    path("cms/theme/", cmsv.ThemeSettingsView.as_view(), name="cms_theme"),
    path("cms/store-profile/", cmsv.StoreProfileView.as_view(), name="cms_store_profile"),
    path("cms/demo-content/remove/", cmsv.DemoContentRemoveView.as_view(), name="demo_remove"),
    path("cms/demo-content/import/", cmsv.DemoContentImportView.as_view(), name="demo_import"),

    # Domains
    # Store plan & billing (owner / manager)
    path("plan/", planv.PlanView.as_view(), name="store_plan"),
    path("plan/change/", planv.PlanChangeView.as_view(), name="store_plan_change"),
    path("plan/invoice/<int:pk>/pay/", planv.InvoicePayStartView.as_view(), name="invoice_pay_start"),
    path("plan/invoice/<int:pk>/confirm/", planv.InvoicePayConfirmView.as_view(), name="invoice_pay_confirm"),

    # Store provisioning (platform owner / platform manager)
    path("stores/", storev.StoreListView.as_view(), name="stores"),
    path("stores/new/", storev.StoreCreateView.as_view(), name="store_create"),
    path("stores/<int:pk>/", storev.StoreDetailView.as_view(), name="store_detail"),
    path("stores/<int:pk>/members/add/", storev.StoreMemberAddView.as_view(), name="store_member_add"),
    path("stores/<int:pk>/switch/", storev.StoreSwitchView.as_view(), name="store_switch"),

    # Platform billing (super admin)
    path("billing/", billv.BillingDashboardView.as_view(), name="billing"),
    path("billing/plans/", billv.PlanListView.as_view(), name="billing_plans"),
    path("billing/plans/<int:pk>/", billv.PlanEditView.as_view(), name="billing_plan_edit"),
    path("billing/subscriptions/", billv.SubscriptionListView.as_view(), name="billing_subscriptions"),
    path("billing/commissions/", billv.CommissionListView.as_view(), name="billing_commissions"),
    path("billing/commissions/<int:pk>/paid/", billv.CommissionMarkPaidView.as_view(), name="billing_commission_paid"),
    path("billing/settings/", billv.BillingSettingsView.as_view(), name="billing_settings"),

    path("domains/", domv.DomainListView.as_view(), name="domains"),
    path("domains/add/", domv.DomainAddView.as_view(), name="domain_add"),
    path("domains/<int:pk>/verify/", domv.DomainVerifyView.as_view(), name="domain_verify"),
    path("domains/<int:pk>/primary/", domv.DomainPrimaryView.as_view(), name="domain_primary"),
    path("domains/<int:pk>/delete/", domv.DomainDeleteView.as_view(), name="domain_delete"),

    # SEO
    path("seo/", seov.SeoSettingsView.as_view(), name="seo_settings"),
    path("seo/redirects/", seov.RedirectListView.as_view(), name="seo_redirects"),
    path("seo/redirects/new/", seov.RedirectCreateView.as_view(), name="seo_redirect_create"),
    path("seo/redirects/<int:pk>/", seov.RedirectUpdateView.as_view(), name="seo_redirect_edit"),
    path("seo/redirects/<int:pk>/delete/", seov.RedirectDeleteView.as_view(), name="seo_redirect_delete"),
    path("seo/meta/new/", seov.SeoMetaCreateView.as_view(), name="seo_meta_create"),
    path("seo/meta/<int:pk>/", seov.SeoMetaUpdateView.as_view(), name="seo_meta_edit"),
    path("seo/meta/<int:pk>/delete/", seov.SeoMetaDeleteView.as_view(), name="seo_meta_delete"),
]
