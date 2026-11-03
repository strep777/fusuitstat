/**
 * 演示内容的统一标题处理。
 *
 * 后端在演示数据的标题末尾追加标记，运营录入的真实内容不会带它。
 * 列表与详情必须用同一份函数，避免「卡片没有后缀、详情还有」的割裂。
 */
export const DEMO_TITLE_MARK = "（演示内容）";

export function displayTitle(title: string | null | undefined): string {
  return (title ?? "").replace(DEMO_TITLE_MARK, "").trim();
}

export function isDemoTitle(title: string | null | undefined): boolean {
  return (title ?? "").includes(DEMO_TITLE_MARK);
}
