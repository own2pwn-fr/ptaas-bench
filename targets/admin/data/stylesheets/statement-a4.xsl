<?xml version="1.0" encoding="UTF-8"?>
<!--
  Full A4 statement layout. Portrait, 210mm by 297mm, seven column line table
  including the running balance and the ageing summary. This is the layout used
  for the statements attached to the nightly run.
-->
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">

  <xsl:output method="html" encoding="UTF-8" indent="yes"
              doctype-system="about:legacy-compat"/>

  <xsl:template match="/statement">
    <html lang="en">
      <head>
        <meta charset="UTF-8"/>
        <title>
          <xsl:text>Statement </xsl:text>
          <xsl:value-of select="@number"/>
          <xsl:text> - </xsl:text>
          <xsl:value-of select="header/account/name"/>
        </title>
        <style>
          @page { size: A4 portrait; margin: 18mm 16mm; }
          body { font-family: "Source Sans Pro", Arial, sans-serif; font-size: 10pt; color: #1d1d1b; }
          h1 { font-size: 16pt; margin: 0 0 2mm 0; }
          .meta { width: 100%; margin-bottom: 8mm; }
          .meta td { vertical-align: top; padding: 0 6mm 1mm 0; }
          table.lines { width: 100%; border-collapse: collapse; }
          table.lines th { text-align: left; border-bottom: 1px solid #1d1d1b; padding: 1.5mm 2mm; font-size: 9pt; }
          table.lines td { border-bottom: 1px solid #d8d8d4; padding: 1.5mm 2mm; }
          td.num, th.num { text-align: right; white-space: nowrap; }
          tr.credit td, tr.payment td { color: #4a6b3f; }
          .totals { margin-top: 6mm; width: 90mm; margin-left: auto; border-collapse: collapse; }
          .totals td { padding: 1mm 2mm; border-bottom: 1px solid #d8d8d4; }
          .totals tr.closing td { font-weight: bold; border-bottom: 2px solid #1d1d1b; }
          .ageing { margin-top: 6mm; border-collapse: collapse; }
          .ageing th, .ageing td { border: 1px solid #d8d8d4; padding: 1mm 3mm; font-size: 9pt; }
          .footer { margin-top: 10mm; font-size: 8.5pt; color: #55554f; }
        </style>
      </head>
      <body>
        <h1>
          <xsl:text>Statement of account </xsl:text>
          <xsl:value-of select="@number"/>
        </h1>

        <table class="meta">
          <tr>
            <td>
              <strong><xsl:value-of select="header/account/legalName"/></strong><br/>
              <xsl:for-each select="header/account/address/line">
                <xsl:value-of select="."/><br/>
              </xsl:for-each>
              <xsl:value-of select="header/account/address/city"/><br/>
              <xsl:value-of select="header/account/address/postcode"/><br/>
              <xsl:value-of select="header/account/address/country"/>
            </td>
            <td>
              <xsl:text>Account reference: </xsl:text>
              <xsl:value-of select="header/account/@reference"/><br/>
              <xsl:text>Period: </xsl:text>
              <xsl:value-of select="header/period/@from"/>
              <xsl:text> to </xsl:text>
              <xsl:value-of select="header/period/@to"/><br/>
              <xsl:text>Issued: </xsl:text>
              <xsl:value-of select="@issuedOn"/><br/>
              <xsl:text>Currency: </xsl:text>
              <xsl:value-of select="header/currency"/>
            </td>
            <td>
              <xsl:text>Account manager</xsl:text><br/>
              <xsl:value-of select="header/accountManager/name"/><br/>
              <xsl:value-of select="header/accountManager/email"/><br/>
              <xsl:value-of select="header/accountManager/telephone"/>
            </td>
          </tr>
        </table>

        <table class="lines">
          <thead>
            <tr>
              <th>Date</th>
              <th>Type</th>
              <th>Reference</th>
              <th>Description</th>
              <th class="num">Net</th>
              <th class="num">Tax</th>
              <th class="num">Gross</th>
              <th class="num">Balance</th>
            </tr>
          </thead>
          <tbody>
            <xsl:apply-templates select="lines/line">
              <xsl:sort select="@seq" data-type="number"/>
            </xsl:apply-templates>
          </tbody>
        </table>

        <table class="totals">
          <tr>
            <td>Balance brought forward</td>
            <td class="num"><xsl:value-of select="totals/openingBalance"/></td>
          </tr>
          <tr>
            <td>Invoiced in the period</td>
            <td class="num"><xsl:value-of select="totals/invoiced"/></td>
          </tr>
          <tr>
            <td>Credited in the period</td>
            <td class="num"><xsl:value-of select="totals/credited"/></td>
          </tr>
          <tr>
            <td>Received in the period</td>
            <td class="num"><xsl:value-of select="totals/received"/></td>
          </tr>
          <tr class="closing">
            <td>
              <xsl:text>Balance due (</xsl:text>
              <xsl:value-of select="totals/@currency"/>
              <xsl:text>)</xsl:text>
            </td>
            <td class="num"><xsl:value-of select="totals/closingBalance"/></td>
          </tr>
        </table>

        <table class="ageing">
          <tr>
            <xsl:for-each select="totals/ageing/bucket">
              <th><xsl:value-of select="@range"/></th>
            </xsl:for-each>
          </tr>
          <tr>
            <xsl:for-each select="totals/ageing/bucket">
              <td class="num"><xsl:value-of select="@amount"/></td>
            </xsl:for-each>
          </tr>
        </table>

        <div class="footer">
          <p><xsl:value-of select="footer/note"/></p>
          <p>
            <xsl:text>Remit to </xsl:text>
            <xsl:value-of select="header/remitTo/bank"/>
            <xsl:text>, IBAN </xsl:text>
            <xsl:value-of select="header/remitTo/iban"/>
            <xsl:text>, BIC </xsl:text>
            <xsl:value-of select="header/remitTo/bic"/>
            <xsl:text>, quoting </xsl:text>
            <xsl:value-of select="header/remitTo/paymentReference"/>
            <xsl:text>.</xsl:text>
          </p>
          <p><xsl:value-of select="footer/issuer"/></p>
        </div>
      </body>
    </html>
  </xsl:template>

  <xsl:template match="line">
    <tr>
      <xsl:attribute name="class"><xsl:value-of select="@type"/></xsl:attribute>
      <td><xsl:value-of select="@date"/></td>
      <td><xsl:value-of select="@type"/></td>
      <td><xsl:value-of select="@reference"/></td>
      <td><xsl:value-of select="description"/></td>
      <td class="num"><xsl:value-of select="net"/></td>
      <td class="num"><xsl:value-of select="tax"/></td>
      <td class="num"><xsl:value-of select="gross"/></td>
      <td class="num"><xsl:value-of select="balance"/></td>
    </tr>
  </xsl:template>

</xsl:stylesheet>
