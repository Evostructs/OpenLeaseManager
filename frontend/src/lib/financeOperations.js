export const financeKinds={rent:'租金',property_fee:'物业费',deposit:'押金',expense:'费用付款'}
export const financeStates={missing:'未提供',provided:'已提供',zero:'确认零发生'}
// RFC 4180-style quoted fields, including commas and newlines in references.
export function parseBankCsv(text){
 if(text.length>1000000)throw new Error('文件过大，每批最多 500 条、1 MB')
 const rows=[];let row=[],cell='',quoted=false
 for(let i=0;i<text.length;i++){
  const c=text[i]
  if(c==='"'){if(quoted&&text[i+1]==='"'){cell+='"';i++}else if(quoted||!cell)quoted=!quoted;else throw new Error('CSV 引号格式不正确')}
  else if(c===','&&!quoted){row.push(cell);cell=''}
  else if((c==='\n'||c==='\r')&&!quoted){if(c==='\r'&&text[i+1]==='\n')i++;row.push(cell);if(row.some(s=>s.trim()))rows.push(row);row=[];cell=''}
  else cell+=c
 }
 if(quoted)throw new Error('CSV 引号未闭合')
 row.push(cell);if(row.some(s=>s.trim()))rows.push(row)
 const expected=['流水编号','日期','方向','金额','往来方','摘要','账户简称']
 if(!rows.length||rows[0].map(s=>s.replace(/^\uFEFF/,'').trim()).join('|')!==expected.join('|'))throw new Error('请使用模板列名：'+expected.join('、'))
 const seen=new Set()
 return rows.slice(1).map((r,index)=>{
  if(index>=500)throw new Error('每批最多导入 500 条')
  if(r.length!==7)throw new Error(`第 ${index+2} 行应为 7 列`)
  const [import_key,occurred_on,direction,amount,counterparty,reference,account_label]=r.map(s=>s.trim())
  if(!import_key||seen.has(import_key))throw new Error(`第 ${index+2} 行流水编号为空或重复`)
  seen.add(import_key)
  if(!/^\d{4}-\d{2}-\d{2}$/.test(occurred_on)||!['收入','支出','in','out'].includes(direction)||!/^\d+(\.\d{1,2})?$/.test(amount)||Number(amount)<=0||!counterparty)throw new Error(`第 ${index+2} 行日期、方向、金额或往来方无效`)
  return {import_key,occurred_on,direction:['收入','in'].includes(direction)?'in':'out',amount,counterparty,reference,account_label}
 })
}
