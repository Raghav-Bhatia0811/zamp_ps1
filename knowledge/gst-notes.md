# GST notes for AP staff

Internal summary to help AP read Indian GST invoices. Not legal advice; the tax team has the final word.

## §G1 GSTIN

### §G1.1 Structure
A GSTIN has 15 characters: a 2-digit state code (27 is Maharashtra, 29 Karnataka, 07 Delhi), the supplier's 10-character PAN (5 letters, 4 digits, 1 letter), an entity number (1-9 then A-Z), the letter Z by default, and a final check character. The check character is computed from the first 14 characters (a mod-36 checksum), so a single misread character almost always makes a GSTIN invalid.

### §G1.2 Using the check character
Because of the check character, a GSTIN read from a scan can be verified without calling anyone: if the check fails, the read is wrong. Common OCR slips are 0/O, 1/I, 5/S, 8/B and 2/Z. A corrected GSTIN that passes the check and exists in the supplier master can be accepted.

## §G2 Tax invoice contents

### §G2.1 Required particulars
Rule 46 of the CGST Rules requires a tax invoice to show: supplier name, address and GSTIN; a consecutive serial number of up to 16 characters, unique for the financial year; date of issue; recipient name, address and GSTIN; HSN or SAC codes; description, quantity and value of goods or services; taxable value; tax rate and amount for each of CGST, SGST/UTGST or IGST; place of supply; and the signature or digital signature of the supplier or an authorised representative.

### §G2.2 E-invoices
Businesses above the e-invoicing turnover threshold (₹5 crore aggregate turnover since August 2023) must report B2B invoices to the Invoice Registration Portal. A valid e-invoice carries an IRN and a signed QR code; it needs no physical signature.

## §G3 Which tax applies

### §G3.1 CGST and SGST vs IGST
When the supplier and the place of supply are in the same state, the invoice charges CGST and SGST in equal halves (for example 9% + 9% for an 18% rate). When they are in different states, it charges IGST at the full rate. Halden Foods is registered in Maharashtra (27), so a Maharashtra supplier should charge CGST + SGST and a Karnataka or Delhi supplier should charge IGST.

## §G4 Input tax credit

### §G4.1 Conditions
Halden can claim input tax credit only if it holds a valid tax invoice, has received the goods or services, the supplier has paid the tax and filed its returns, and the invoice appears in Halden's GSTR-2B (CGST Act, section 16(2)). A wrong GSTIN on the invoice usually means the credit will not appear and is lost.

### §G4.2 180-day rule
If Halden does not pay the supplier within 180 days of the invoice date, credit already claimed must be reversed with interest until payment is made.
