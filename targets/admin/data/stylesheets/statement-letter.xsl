<?xml version="1.0" encoding="UTF-8"?>
<!--
  Condensed US letter layout, 8.5in by 11in. Four column line table: the net and
  tax columns are dropped and only the gross movement and the running balance are
  shown, which is what the North American desks ask for.
-->
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">

  <xsl:output method="html" encoding="UTF-8" indent="yes"
              doctype-system="about:legacy-compat"/>

  <xsl:template match="/statement">
    <html lang="en">
      <head>
        <meta charset="UTF-8"/>
        <title>
          <xsl:text>Account statement </xsl:text>
          <xsl:value-of select="@number"/>
        </title>
        <style>
          @page { size: letter portrait; margin: 0.75in 0.7in; }
          body { font-family: Georgia, "Times New Roman", serif; font-size: 11pt; color: #22221f; }
          h1 { font-size: 15pt; margin: 0 0 0.1in 0; }
          .addr { float: left; width: 3.4in; }
          .info { float: right; width: 3.0in; text-align: right; }
          .clear { clear: both; }
          table.lines { width: 100%; border-collapse: collapse; margin-top: 0.35in; }
          table.lines th { text-align: left; border-bottom: 2px solid #22221f; padding: 0.06in 0.08in; font-size: 10pt; }
          table.lines td { border-bottom: 1px dotted #a8a8a2; padding: 0.06in 0.08in; }
          td.num, th.num { text-align: right; white-space: nowrap; }
          .due { margin-top: 0.3in; font-size: 12pt; text-align: right; }
          .due strong { border-top: 2px solid #22221f; padding-top: 0.06in; }
          .remit { margin-top: 0.4in; font-size: 9.5pt; }
          .footer { margin-top: 0.35in; font-size: 9pt; color: #55554f; }
        </style>
      </head>
      <body>
        <h1>
          <xsl:value-of select="header/account/name"/>
          <xsl:text> - statement </xsl:text>
          <xsl:value-of select="@number"/>
        </h1>

        <div class="addr">
          <xsl:value-of select="header/account/legalName"/><br/>
          <xsl:for-each select="header/account/address/line">
            <xsl:value-of select="."/><br/>
          </xsl:for-each>
          <xsl:value-of select="header/account/address/city"/>
          <xsl:text> </xsl:text>
          <xsl:value-of select="header/account/address/postcode"/><br/>
          <xsl:value-of select="header/account/address/country"/>
        </div>

        <div class="info">
          <xsl:text>Reference </xsl:text>
          <xsl:value-of select="header/account/@reference"/><br/>
          <xsl:value-of select="header/period/@from"/>
          <xsl:text> through </xsl:text>
          <xsl:value-of select="header/period/@to"/><br/>
          <xsl:text>Issued </xsl:text>
          <xsl:value-of select="@issuedOn"/><br/>
          <xsl:value-of select="header/accountManager/name"/><br/>
          <xsl:value-of select="header/accountManager/email"/>
        </div>

        <div class="clear"/>

        <table class="lines">
          <thead>
            <tr>
              <th>Date</th>
              <th>Reference</th>
              <th>Detail</th>
              <th class="num">
                <xsl:text>Amount (</xsl:text>
                <xsl:value-of select="header/currency"/>
                <xsl:text>)</xsl:text>
              </th>
              <th class="num">Balance</th>
            </tr>
          </thead>
          <tbody>
            <xsl:apply-templates select="lines/line">
              <xsl:sort select="@seq" data-type="number"/>
            </xsl:apply-templates>
          </tbody>
        </table>

        <div class="due">
          <strong>
            <xsl:text>Amount due: </xsl:text>
            <xsl:value-of select="totals/@currency"/>
            <xsl:text> </xsl:text>
            <xsl:value-of select="totals/closingBalance"/>
          </strong>
        </div>

        <div class="remit">
          <xsl:text>Please remit to </xsl:text>
          <xsl:value-of select="header/remitTo/bank"/>
          <xsl:text>, IBAN </xsl:text>
          <xsl:value-of select="header/remitTo/iban"/>
          <xsl:text>, BIC </xsl:text>
          <xsl:value-of select="header/remitTo/bic"/>
          <xsl:text>. Payment reference </xsl:text>
          <xsl:value-of select="header/remitTo/paymentReference"/>
          <xsl:text>.</xsl:text>
        </div>

        <div class="footer">
          <xsl:value-of select="footer/issuer"/>
        </div>
      </body>
    </html>
  </xsl:template>

  <xsl:template match="line">
    <tr>
      <td><xsl:value-of select="@date"/></td>
      <td><xsl:value-of select="@reference"/></td>
      <td>
        <xsl:value-of select="description"/>
        <xsl:if test="@type = 'credit'">
          <xsl:text> (credit)</xsl:text>
        </xsl:if>
      </td>
      <td class="num"><xsl:value-of select="gross"/></td>
      <td class="num"><xsl:value-of select="balance"/></td>
    </tr>
  </xsl:template>

</xsl:stylesheet>
