xLab = 'White Salmon at Underwood(feet)'
yLab = 'White Salmon at Husum(feet)'

fn = 'data'

a = read.table(fn, head=T)
a$date = as.Date(as.character(a$date))
a$year = as.integer(format(a$date, "%Y"))

z = lm(gauge ~ feet, data = a)
zsc = summary(z)$coefficients
zse = zsc[,'Estimate']
zst = zsc[,'t value']
zsn = names(zse)
legend = sprintf("%13s %.3g %.3g", zsn, zse, zst)

plot(a$feet, a$gauge, xlab=xLab, ylab=yLab, sub=date(), 
     col=((a$year - min(a$year)) + 3)) 
grid()
legend('topleft', legend=legend)
lines(a$feet, z$fitted.values, col=2)

dev.copy2pdf(file='fit.pdf', paper='letter')

print(summary(z))
