-- Contract templates and reusable service provider details.
-- Run after upgrade_v6.sql in Navicat. Safe to rerun without restoring deleted samples.
BEGIN;

CREATE TABLE IF NOT EXISTS contract_templates (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seed_key VARCHAR(40) UNIQUE,
    title VARCHAR(100) NOT NULL CHECK (length(btrim(title)) > 0),
    description VARCHAR(255) NOT NULL DEFAULT '',
    body TEXT NOT NULL CHECK (length(btrim(body)) > 0 AND length(body) <= 20000),
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS contract_templates_active_idx
    ON contract_templates (id) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS contract_party_profile (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    provider_name VARCHAR(150) NOT NULL DEFAULT '',
    provider_tax_id VARCHAR(64) NOT NULL DEFAULT '',
    provider_contact VARCHAR(100) NOT NULL DEFAULT '',
    provider_phone VARCHAR(100) NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO contract_party_profile (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

INSERT INTO contract_templates (seed_key, title, description, body) VALUES
('bookkeeping_tax', '代理记账及纳税申报服务合同',
 '适合按月或按年持续提供记账、约定税种申报服务；申报范围需签约前确认。',
$template$
合同编号：{{contract_number}}
委托方（甲方）：{{customer_name}}　统一社会信用代码：{{customer_tax_id}}
联系人：{{customer_contact}}　电话：{{customer_phone}}
受托方（乙方）：{{provider_name}}　统一社会信用代码：{{provider_tax_id}}
联系人：{{provider_contact}}　电话：{{provider_phone}}

一、服务内容及期限
甲方委托乙方办理{{service_name}}。服务期间为{{service_start_month}}至{{service_end_month}}。乙方依据甲方交付的真实、完整资料开展代理记账；代理纳税申报的税种、频率及具体事项由双方在签署时列明：________________________________。本合同不当然包含税款支付、发票代开、审计或税务争议代理等未列明事项。

二、资料传递与责任
甲方应在每月{{materials_day}}日前整理并交付原始凭证、银行流水及其他必要资料，双方按资料清单签收；电子资料应记录交付日期、文件名称及接收人。甲方对资料的真实性、完整性、合法性负责，并及时补正退回的资料。乙方对收到的资料妥善保管，依据约定完成记账与申报工作；因一方原因造成延误或差错，由相应责任方承担责任。资料延迟交付时，双方应及时书面确认调整后的工作安排。

三、成果、档案与保密
乙方按双方确认的方式提供账簿、财务报表及申报结果资料。财务报表提供的频率、日期和方式：________________________________。会计档案的保管地点、移交周期与交接清单由双方确认：________________________________。乙方对服务中获悉的商业秘密和个人信息保密；法律规定或甲方书面授权的情形除外。

四、费用与付款
本订单服务费共人民币{{amount}}元。付款约定：{{payment_terms}}。约定付款日期：{{due_date}}。额外服务须经双方另行确认范围与费用。

五、期限变更、终止与交接
服务期限届满后如需续约，双方另行确认。提前终止或变更服务范围时，应书面约定费用结算、未完成事项处理及资料交接；乙方按清单交还甲方资料和电子数据，双方确认交接完成。

六、违约与争议
一方未按约履行义务，应及时采取补救措施并承担依法及依约应承担的责任。争议先协商解决；协商不成，依法向有管辖权的人民法院起诉。双方可另行约定争议解决方式：________________________________。

七、补充约定
{{extra_terms}}

本合同经双方签字或盖章后生效；未尽事宜由双方书面补充。双方各执一份，具有同等效力。

甲方（签字／盖章）：________________　乙方（签字／盖章）：________________
签订日期：{{sign_date}}　签订地点：________________
$template$),
('bookkeeping_only', '代理记账服务合同（不含纳税申报）',
 '适合仅委托记账、报表和档案整理的订单，申报由客户另行安排。',
$template$
合同编号：{{contract_number}}
委托方（甲方）：{{customer_name}}　统一社会信用代码：{{customer_tax_id}}
联系人：{{customer_contact}}　电话：{{customer_phone}}
受托方（乙方）：{{provider_name}}　统一社会信用代码：{{provider_tax_id}}
联系人：{{provider_contact}}　电话：{{provider_phone}}

一、委托范围
甲方委托乙方办理{{service_name}}，服务期间为{{service_start_month}}至{{service_end_month}}。乙方依据甲方交付的资料进行原始凭证审核、记账凭证编制、账簿登记及约定财务报表编制。本合同不包含纳税申报、税款支付或其他未书面列明的业务；这些事项由甲方自行处理或另行委托。

二、资料和成果交接
甲方每月{{materials_day}}日前按清单交付原始凭证及相关资料，并对真实性、完整性、合法性负责。乙方签收、保管并在服务完成后按清单交付账簿、报表及电子数据；具体交付时间与方式：________________________________。资料缺失或有误时，乙方应告知甲方，甲方应及时补正。终止合同时双方办理会计资料与档案交接。

三、收费
服务费共人民币{{amount}}元；付款约定：{{payment_terms}}；约定付款日期：{{due_date}}。超出约定范围的工作须另行确认费用。

四、保密、责任和争议
乙方对获悉的商业秘密和个人信息保密。双方分别对自身提供资料或履行服务中的过错承担相应责任。变更或提前终止应书面确认费用结算和资料交接。争议先协商，协商不成依法向有管辖权的人民法院起诉。

五、补充约定
{{extra_terms}}

本合同经双方签字或盖章后生效，双方各执一份。

甲方（签字／盖章）：________________　乙方（签字／盖章）：________________
签订日期：{{sign_date}}　签订地点：________________
$template$),
('setup_backlog', '建账及历史账务整理专项服务合同',
 '适合一次性建账、期初资料整理或约定月份的补账工作。',
$template$
合同编号：{{contract_number}}
委托方（甲方）：{{customer_name}}　统一社会信用代码：{{customer_tax_id}}
联系人：{{customer_contact}}　电话：{{customer_phone}}
受托方（乙方）：{{provider_name}}　统一社会信用代码：{{provider_tax_id}}
联系人：{{provider_contact}}　电话：{{provider_phone}}

一、专项范围与交付
甲方委托乙方完成{{service_name}}。拟处理期间为{{service_start_month}}至{{service_end_month}}。具体账套、会计制度、期初余额来源、历史凭证范围、交付清单及验收标准由双方填写：________________________________。不在清单中的后续月度代理记账、税务申报和审计服务不属于本合同范围。

二、资料提供与核对
甲方按约定提供真实、完整、合法的历史凭证、银行流水、资产负债资料及其他必要资料，并对期初数据予以确认。双方以清单记录资料交付与退补；电子文件应留存名称、日期及接收人。资料缺失、矛盾或无法核实的事项，由双方书面记录处理方法及对交付时间的影响。

三、费用、交付和保管
服务费共人民币{{amount}}元；付款约定：{{payment_terms}}；约定付款日期：{{due_date}}。乙方交付账套数据、凭证整理结果和交接清单的方式与日期：________________________________。双方明确档案保管方及电子数据移交格式；终止或完成时办理资料交接。

四、保密、责任与争议
乙方对工作中知悉的商业秘密和个人信息保密。双方分别对自身资料或服务中的过错承担相应责任。变更范围和费用须书面确认；争议先协商，协商不成依法向有管辖权的人民法院起诉。

五、补充约定
{{extra_terms}}

本合同经双方签字或盖章后生效，双方各执一份。

甲方（签字／盖章）：________________　乙方（签字／盖章）：________________
签订日期：{{sign_date}}　签订地点：________________
$template$)
ON CONFLICT (seed_key) DO NOTHING;

COMMIT;
