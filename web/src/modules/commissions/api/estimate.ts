/**
 * 公开参考估价 API（FR-026）。
 *
 * 无需登录；只读取公开配置并提交回访信息。最终报价由工作室人工确认，
 * 本接口返回的区间不写入订单金额。
 */
import { api } from "@/shared/lib/http";

export interface EstimateOptionSetting {
  value: string;
  label: string;
  price_cents: number;
}

export interface EstimateStyleSetting {
  value: string;
  label: string;
  factor: number;
}

export interface EstimateSeries {
  slug: string;
  code: string;
  title: string;
  base_price_cents: number;
  currency: string;
  lead_time_note?: string | null;
}

export interface EstimateOptions {
  response_workdays: number;
  currency: string;
  range_high_percent: number;
  styles: EstimateStyleSetting[];
  options: EstimateOptionSetting[];
  series: EstimateSeries[];
  disclaimer: string;
}

export type EstimateContactType = "email" | "phone" | "wechat";

export interface EstimateRequestInput {
  name: string;
  contact: string;
  contact_type: EstimateContactType;
  series_code: string;
  style: string;
  options: string[];
  character_name: string;
  species: string;
  note: string;
  consent: boolean;
  website: string;
}

export interface EstimateRequestCreated {
  id: string;
  estimate_low_cents: number;
  estimate_high_cents: number;
  currency: string;
  response_workdays: number;
}

export async function fetchEstimateOptions(): Promise<EstimateOptions> {
  return api.get<EstimateOptions>("/estimates/options");
}

export async function submitEstimate(
  payload: EstimateRequestInput,
): Promise<EstimateRequestCreated> {
  return api.post<EstimateRequestCreated>("/estimates", payload);
}
